#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
port_hierarchy_rename.py -- Chip Design Port Hierarchy Rename Tool

Renames a port in a Verilog module and automatically propagates the change
upward through all hierarchy levels, as defined by a VC (filelist) file.

The tool parses the VC file to discover all Verilog sources, builds the design
hierarchy from instantiation relationships, then traces the target port upward
through every parent module -- renaming connected signals, wire/reg declarations,
port declarations, and instance port-connection names along the way.

Requirements: Python 3.6+
"""

from __future__ import print_function

import re
import os
import sys
import shutil
import argparse
from collections import defaultdict, OrderedDict

if sys.version_info < (3, 6):
    print("ERROR: Python 3.6 or later is required (f-string support).")
    print("Current version: Python {}.{}.{}".format(*sys.version_info[:3]))
    sys.exit(1)


VERILOG_KEYWORDS = frozenset({
    'module', 'endmodule', 'input', 'output', 'inout',
    'wire', 'reg', 'logic', 'integer', 'real', 'time', 'realtime',
    'assign', 'always', 'always_ff', 'always_comb', 'always_latch',
    'initial', 'if', 'else', 'begin', 'end',
    'case', 'casex', 'casez', 'endcase', 'default',
    'for', 'while', 'repeat', 'forever', 'do',
    'function', 'endfunction', 'task', 'endtask',
    'generate', 'endgenerate', 'genvar',
    'parameter', 'localparam', 'defparam',
    'posedge', 'negedge', 'or', 'and', 'not', 'xor', 'nand', 'nor',
    'buf', 'bufif0', 'bufif1', 'notif0', 'notif1',
    'pullup', 'pulldown', 'supply0', 'supply1',
    'tri', 'tri0', 'tri1', 'triand', 'trior', 'trireg',
    'wand', 'wor', 'signed', 'unsigned',
    'disable', 'deassign', 'force', 'release',
    'fork', 'join', 'join_any', 'join_none',
    'wait', 'event', 'specify', 'endspecify',
    'primitive', 'endprimitive', 'table', 'endtable',
    'macromodule', 'config', 'endconfig',
})


# ============================================================
#  VC / Filelist Parser
# ============================================================

def parse_vc_file(vc_path, _visited=None):
    """Parse a VC/filelist file and return an ordered list of Verilog source paths."""
    if _visited is None:
        _visited = set()

    vc_abs = os.path.abspath(vc_path)
    if vc_abs in _visited:
        return []
    _visited.add(vc_abs)

    if not os.path.isfile(vc_abs):
        print("[ERROR] VC file not found: {}".format(vc_abs), file=sys.stderr)
        return []

    base_dir = os.path.dirname(vc_abs)
    files = []

    with open(vc_abs, 'r', encoding='utf-8', errors='replace') as fh:
        for raw_line in fh:
            line = raw_line.split('//')[0].strip()
            if not line:
                continue

            if line.startswith(('+incdir+', '+define+', '+libext+', '-y ')):
                continue

            if line.startswith('-f ') or line.startswith('-F '):
                nested = line.split(None, 1)[1].strip()
                if not os.path.isabs(nested):
                    nested = os.path.join(base_dir, nested)
                files.extend(parse_vc_file(nested, _visited))
                continue

            if line.startswith('-v '):
                fpath = line[3:].strip()
            else:
                fpath = line

            if not os.path.isabs(fpath):
                fpath = os.path.join(base_dir, fpath)
            fpath = os.path.normpath(fpath)

            if os.path.isfile(fpath):
                files.append(os.path.abspath(fpath))
            else:
                print("  [WARN] File not found, skipping: {}".format(fpath))

    return files


# ============================================================
#  Verilog Parser
# ============================================================

class Port:
    """Represents a module port with name, direction, and optional width."""
    __slots__ = ('name', 'direction', 'width')

    def __init__(self, name, direction, width=''):
        self.name = name
        self.direction = direction
        self.width = width

    def __repr__(self):
        w = " " + self.width if self.width else ""
        return "{}{}  {}".format(self.direction, w, self.name)


class Module:
    """Represents a parsed Verilog module."""

    def __init__(self, name, filepath):
        self.name = name
        self.filepath = filepath
        self.ports = OrderedDict()      # name -> Port
        self.wires = OrderedDict()      # name -> {'kind': str, 'width': str}
        self.instances = []             # list of dicts

    def __repr__(self):
        return "Module({}, ports={})".format(self.name, list(self.ports.keys()))


def strip_comments(text):
    """Remove Verilog block and line comments."""
    text = re.sub(
        r'/\*.*?\*/',
        lambda m: '\n' * m.group().count('\n'),
        text,
        flags=re.DOTALL,
    )
    text = re.sub(r'//[^\n]*', '', text)
    return text


def _skip_ws(text, pos):
    """Advance past whitespace characters starting at *pos*."""
    n = len(text)
    while pos < n and text[pos] in ' \t\n\r':
        pos += 1
    return pos


def _match_paren(text, pos):
    """From *pos* (pointing at '(') find the matching ')'. Returns index after ')'."""
    depth = 0
    i = pos
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


# Regex that matches a Verilog identifier (letter or underscore, then word chars).
_IDENT_RE = re.compile(r'[A-Za-z_]\w*')


def _parse_instances(body):
    """Extract module instantiations from a (comment-stripped) module body.

    Scans for patterns like:
        module_type  [#(...)]  inst_name  ( .port(signal), ... );
    Uses _IDENT_RE to only match proper identifiers (not bare numbers).
    """
    instances = []
    pos = 0
    body_len = len(body)

    while pos < body_len:
        m = _IDENT_RE.search(body, pos)
        if m is None:
            break

        word = m.group()
        after_word = m.end()

        if word in VERILOG_KEYWORDS:
            pos = after_word
            continue

        cur = _skip_ws(body, after_word)

        if cur < body_len and body[cur] == '#':
            cur = _skip_ws(body, cur + 1)
            if cur < body_len and body[cur] == '(':
                cur = _match_paren(body, cur)
                cur = _skip_ws(body, cur)

        nm = _IDENT_RE.match(body, cur)
        if nm is None or nm.group() in VERILOG_KEYWORDS:
            pos = max(after_word, cur + 1) if cur < body_len else after_word
            continue

        inst_name_end = nm.end()
        cur = _skip_ws(body, inst_name_end)

        if cur >= body_len or body[cur] != '(':
            pos = max(after_word, cur)
            continue

        paren_end = _match_paren(body, cur)
        conn_text = body[cur + 1:paren_end - 1]

        connections = {}
        for cm in re.finditer(r'\.(\w+)\s*\(\s*([^)]*?)\s*\)', conn_text):
            connections[cm.group(1)] = cm.group(2).strip()

        if connections:
            instances.append({
                'type': word,
                'name': nm.group(),
                'connections': connections,
            })

        pos = paren_end

    return instances


def parse_verilog_modules(file_list):
    """Parse every file in *file_list* and return {module_name: Module}."""
    modules = OrderedDict()

    for filepath in file_list:
        try:
            with open(filepath, 'r', encoding='utf-8', errors='replace') as fh:
                raw = fh.read()
        except IOError as exc:
            print("  [WARN] Cannot read {}: {}".format(filepath, exc))
            continue

        clean = strip_comments(raw)

        for m in re.finditer(r'\bmodule\s+(\w+)', clean):
            mod_name = m.group(1)
            cur = m.end()

            cur = _skip_ws(clean, cur)
            if cur < len(clean) and clean[cur] == '#':
                cur = _skip_ws(clean, cur + 1)
                if cur < len(clean) and clean[cur] == '(':
                    cur = _match_paren(clean, cur)

            cur = _skip_ws(clean, cur)
            if cur >= len(clean) or clean[cur] != '(':
                continue
            port_end = _match_paren(clean, cur)
            port_text = clean[cur + 1:port_end - 1]

            semi_pos = clean.find(';', port_end)
            if semi_pos == -1:
                continue

            em = re.search(r'\bendmodule\b', clean[semi_pos:])
            if em is None:
                continue
            body = clean[semi_pos + 1:semi_pos + em.start()]

            mod = Module(mod_name, filepath)

            # --- ports (ANSI style) ---
            ansi_pat = re.compile(
                r'(input|output|inout)\s+'
                r'(?:wire|reg|logic)?\s*'
                r'(?:signed\s+)?'
                r'(\[[^\]]*\])?\s*'
                r'(\w+)'
            )
            ansi_ports = ansi_pat.findall(port_text)

            if ansi_ports:
                for direction, width, name in ansi_ports:
                    mod.ports[name] = Port(name, direction, width.strip() if width else '')
            else:
                # --- ports (non-ANSI / Verilog-1995 style) ---
                port_names = set(re.findall(r'\w+', port_text))
                decl_pat = re.compile(
                    r'(input|output|inout)\s+'
                    r'(?:wire|reg|logic)?\s*'
                    r'(?:signed\s+)?'
                    r'(\[[^\]]*\])?\s*'
                    r'([\w\s,]+);'
                )
                for dm in decl_pat.finditer(body):
                    direction = dm.group(1)
                    width = dm.group(2).strip() if dm.group(2) else ''
                    for name in re.findall(r'\w+', dm.group(3)):
                        if name in port_names:
                            mod.ports[name] = Port(name, direction, width)

            # --- wire / reg declarations ---
            wire_pat = re.compile(
                r'\b(wire|reg|logic)\s+'
                r'(?:signed\s+)?'
                r'(\[[^\]]*\])?\s*'
                r'([\w\s,]+);'
            )
            for wm in wire_pat.finditer(body):
                kind = wm.group(1)
                width = wm.group(2).strip() if wm.group(2) else ''
                for name in re.findall(r'\w+', wm.group(3)):
                    if name not in mod.ports and name not in VERILOG_KEYWORDS:
                        mod.wires[name] = {'kind': kind, 'width': width}

            # --- instances ---
            mod.instances = _parse_instances(body)

            modules[mod_name] = mod

    return modules


# ============================================================
#  Hierarchy Helpers
# ============================================================

def build_parent_map(modules):
    """Return {child_module_name: [(parent_module_name, instance_dict), ...]}."""
    parents = defaultdict(list)
    for mod_name, mod in modules.items():
        for inst in mod.instances:
            if inst['type'] in modules:
                parents[inst['type']].append((mod_name, inst))
    return dict(parents)


def print_hierarchy(modules, parent_map):
    """Pretty-print the module hierarchy tree."""
    children_of = defaultdict(list)
    for child, plist in parent_map.items():
        for pname, inst in plist:
            children_of[pname].append((inst['name'], child))

    tops = [m for m in modules if m not in parent_map]

    def _tree(mod, prefix='', is_last=True):
        connector = '+-- ' if is_last else '|-- '
        if prefix:
            print("{}{}{}".format(prefix, connector, mod))
        else:
            print("  {}".format(mod))
        ext = prefix + ('    ' if is_last else '|   ')
        kids = children_of.get(mod, [])
        for idx, (_iname, child) in enumerate(kids):
            _tree(child, ext, idx == len(kids) - 1)

    for t in tops:
        _tree(t)


# ============================================================
#  Rename Propagation
# ============================================================

def compute_rename_plan(modules, parent_map, start_module, old_port, new_name):
    """
    BFS upward from *start_module* / *old_port* and collect every rename action.

    Returns a list of action dicts (one per affected module, in bottom-up order).
    """
    plan = OrderedDict()

    plan[start_module] = {
        'module': start_module,
        'file': modules[start_module].filepath,
        'signal_renames': [(old_port, new_name)],
        'inst_port_renames': [],
    }

    queue = [(start_module, old_port)]
    visited = set()

    while queue:
        child_mod, child_port = queue.pop(0)
        key = (child_mod, child_port)
        if key in visited:
            continue
        visited.add(key)

        if child_mod not in parent_map:
            continue

        for parent_name, inst in parent_map[child_mod]:
            if child_port not in inst['connections']:
                continue

            connected_signal = inst['connections'][child_port]

            if parent_name not in plan:
                plan[parent_name] = {
                    'module': parent_name,
                    'file': modules[parent_name].filepath,
                    'signal_renames': [],
                    'inst_port_renames': [],
                }

            action = plan[parent_name]

            action['inst_port_renames'].append((child_mod, child_port, new_name))

            if not re.match(r'^\w+$', connected_signal):
                print("  [INFO] In module '{}', port .{} connects to "
                      "expression '{}' -- only the port-connection name will "
                      "be renamed; the expression is left unchanged.".format(
                          parent_name, child_port, connected_signal))
                continue

            if connected_signal != new_name:
                pair = (connected_signal, new_name)
                if pair not in action['signal_renames']:
                    action['signal_renames'].append(pair)

            parent_mod = modules[parent_name]
            if connected_signal in parent_mod.ports:
                queue.append((parent_name, connected_signal))

    return list(plan.values())


# ============================================================
#  File-Level Rename Application
# ============================================================

def _find_module_span(content, module_name):
    """Return (start, end) character offsets of a 'module ... endmodule' block."""
    pat = re.compile(r'\bmodule\s+' + re.escape(module_name) + r'\b')
    m = pat.search(content)
    if m is None:
        return None, None
    start = m.start()
    em = re.search(r'\bendmodule\b', content[m.end():])
    if em is None:
        return start, len(content)
    return start, m.end() + em.end()


def _rename_inst_port(module_text, child_type, old_port, new_port):
    """
    Within *module_text*, find instantiations of *child_type* and rename
    .old_port( -> .new_port( inside those instantiation blocks only.
    """
    parts = []
    search_start = 0
    header_re = re.compile(r'\b' + re.escape(child_type) + r'\b')

    while search_start < len(module_text):
        hm = header_re.search(module_text, search_start)
        if hm is None:
            parts.append(module_text[search_start:])
            break

        cur = hm.end()
        cur = _skip_ws(module_text, cur)

        if cur < len(module_text) and module_text[cur] == '#':
            cur = _skip_ws(module_text, cur + 1)
            if cur < len(module_text) and module_text[cur] == '(':
                cur = _match_paren(module_text, cur)
                cur = _skip_ws(module_text, cur)

        nm = _IDENT_RE.match(module_text, cur)
        if nm is None or nm.group() in VERILOG_KEYWORDS:
            parts.append(module_text[search_start:hm.end()])
            search_start = hm.end()
            continue
        cur = _skip_ws(module_text, nm.end())

        if cur >= len(module_text) or module_text[cur] != '(':
            parts.append(module_text[search_start:hm.end()])
            search_start = hm.end()
            continue

        paren_end = _match_paren(module_text, cur)
        semi = module_text.find(';', paren_end - 1)
        inst_end = semi + 1 if semi != -1 else paren_end

        inst_block = module_text[hm.start():inst_end]
        inst_block = re.sub(
            r'\.' + re.escape(old_port) + r'(\s*\()',
            '.' + new_port + r'\1',
            inst_block,
        )

        parts.append(module_text[search_start:hm.start()])
        parts.append(inst_block)
        search_start = inst_end

    return ''.join(parts)


def _rename_signal(module_text, old_sig, new_sig):
    """Replace *old_sig* as a standalone identifier (not preceded by '.') in *module_text*."""
    pattern = r'(?<!\.)(?<!\w)' + re.escape(old_sig) + r'(?!\w)'
    return re.sub(pattern, new_sig, module_text)


def apply_plan_to_files(plan, dry_run=False, backup=True):
    """
    Apply the full rename plan to the source files.

    Returns {filepath: new_content} for every file that was changed.
    """
    by_file = defaultdict(list)
    for action in plan:
        by_file[action['file']].append(action)

    results = {}

    for filepath, actions in by_file.items():
        with open(filepath, 'r', encoding='utf-8', errors='replace') as fh:
            content = fh.read()

        original = content

        for action in actions:
            start, end = _find_module_span(content, action['module'])
            if start is None:
                print("  [WARN] Module '{}' not found in {}".format(
                    action['module'], filepath))
                continue

            mod_text = content[start:end]

            for child_type, op, np_ in action['inst_port_renames']:
                mod_text = _rename_inst_port(mod_text, child_type, op, np_)

            for old_s, new_s in action['signal_renames']:
                mod_text = _rename_signal(mod_text, old_s, new_s)

            content = content[:start] + mod_text + content[end:]

        if content != original:
            results[filepath] = content
            if not dry_run:
                if backup:
                    shutil.copy2(filepath, filepath + '.bak')
                with open(filepath, 'w', encoding='utf-8') as fh:
                    fh.write(content)

    return results


# ============================================================
#  Display Helpers
# ============================================================

def display_plan(plan, modules):
    """Print the rename plan in human-readable format."""
    print("")
    print("=" * 64)
    print("  RENAME PLAN")
    print("=" * 64)

    for idx, action in enumerate(plan, 1):
        mod = modules[action['module']]
        print("")
        print("  [{}] Module: {}".format(idx, action['module']))
        print("      File:   {}".format(os.path.relpath(action['file'])))

        for old, new in action['signal_renames']:
            p = mod.ports.get(old)
            w = mod.wires.get(old)
            if p:
                width_str = " " + p.width if p.width else ""
                print("      Port rename:   {}{} {}  ->  {}".format(
                    p.direction, width_str, old, new))
            if w:
                width_str = " " + w['width'] if w['width'] else ""
                print("      Wire rename:   {}{} {}  ->  {}".format(
                    w['kind'], width_str, old, new))
            if not p and not w:
                print("      Signal rename: {}  ->  {}".format(old, new))

        for child_type, old_port, new_port in action['inst_port_renames']:
            print("      Inst port:     {}  .{}()  ->  .{}()".format(
                child_type, old_port, new_port))

    print("")
    print("=" * 64)


def display_diff(filepath, original, modified):
    """Show a minimal line-level diff."""
    old_lines = original.splitlines(keepends=True)
    new_lines = modified.splitlines(keepends=True)

    print("")
    print("--- {}".format(os.path.relpath(filepath)))
    print("+++ {}  (modified)".format(os.path.relpath(filepath)))

    max_n = max(len(old_lines), len(new_lines))
    for i in range(max_n):
        ol = old_lines[i].rstrip('\n') if i < len(old_lines) else ''
        nl = new_lines[i].rstrip('\n') if i < len(new_lines) else ''
        if ol != nl:
            print("  @line {}:".format(i + 1))
            print("    - {}".format(ol))
            print("    + {}".format(nl))


# ============================================================
#  Main
# ============================================================

def _parse_port_spec(spec):
    """Parse 'data_in[7:0]' -> ('data_in', '[7:0]')."""
    m = re.match(r'(\w+)\s*(\[[\d: ]+\])?\s*$', spec.strip())
    if m:
        return m.group(1), (m.group(2) or '').strip()
    return spec.strip(), ''


def main():
    ap = argparse.ArgumentParser(
        description=(
            'Chip-design port hierarchy rename tool.\n'
            'Renames a port and propagates the change through all hierarchy levels.'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            'Examples\n'
            '--------\n'
            '  %(prog)s -m leaf_cell -p data_in -n rx_data -vc project.vc\n'
            '  %(prog)s -m leaf_cell -p "data_in[7:0]" -n rx_data -vc project.vc --dry-run\n'
            '  %(prog)s -m leaf_cell -p data_in -n rx_data -vc project.vc --no-backup -y\n'
        ),
    )

    ap.add_argument('-m', '--module',
                    help='Module name that owns the port to rename')
    ap.add_argument('-p', '--port',
                    help='Current port name (may include width, e.g. data_in[7:0])')
    ap.add_argument('-n', '--new-name',
                    help='New port / signal name')
    ap.add_argument('-vc', '--vc-file',
                    help='Path to VC / filelist file')
    ap.add_argument('--dry-run', action='store_true',
                    help='Show what would change without modifying any file')
    ap.add_argument('--no-backup', action='store_true',
                    help='Skip creating .bak backup files')
    ap.add_argument('-y', '--yes', action='store_true',
                    help='Skip interactive confirmation')
    ap.add_argument('--show-hierarchy', action='store_true',
                    help='Print the parsed hierarchy tree and exit')

    args = ap.parse_args()

    # --- VC file is always needed ---
    if not args.vc_file:
        args.vc_file = input('VC file path: ').strip()

    # ---- 1. VC file ----
    print("\n[1/5] Parsing VC file: {}".format(args.vc_file))
    verilog_files = parse_vc_file(args.vc_file)
    if not verilog_files:
        print("[ERROR] No Verilog files found.", file=sys.stderr)
        sys.exit(1)
    print("  Found {} source file(s)".format(len(verilog_files)))

    # ---- 2. Parse Verilog ----
    print("\n[2/5] Parsing Verilog modules ...")
    modules = parse_verilog_modules(verilog_files)
    if not modules:
        print("[ERROR] No modules parsed.", file=sys.stderr)
        sys.exit(1)
    print("  Found {} module(s): {}".format(len(modules), ', '.join(modules.keys())))

    # ---- 3. Build hierarchy ----
    print("\n[3/5] Building hierarchy ...")
    parent_map = build_parent_map(modules)
    print("\n  Design hierarchy:")
    print_hierarchy(modules, parent_map)

    if args.show_hierarchy:
        sys.exit(0)

    # ---- collect remaining args (interactive fallback) ----
    if not args.module:
        args.module = input('Module name: ').strip()
    if not args.port:
        args.port = input('Port name (e.g. data_in or data_in[7:0]): ').strip()
    if not args.new_name:
        args.new_name = input('New port name: ').strip()

    port_name, _ = _parse_port_spec(args.port)
    new_name = args.new_name.strip()
    module_name = args.module.strip()
    if module_name.endswith('.v'):
        module_name = module_name[:-2]

    # ---- validate ----
    if module_name not in modules:
        print("\n[ERROR] Module '{}' not found.  Available: {}".format(
            module_name, ', '.join(modules.keys())), file=sys.stderr)
        sys.exit(1)

    mod = modules[module_name]
    if port_name not in mod.ports:
        print("\n[ERROR] Port '{}' not in module '{}'.".format(
            port_name, module_name), file=sys.stderr)
        print("  Available ports: {}".format(', '.join(mod.ports.keys())),
              file=sys.stderr)
        sys.exit(1)

    if port_name == new_name:
        print("\n[INFO] Old name and new name are identical -- nothing to do.")
        sys.exit(0)

    # ---- 4. Plan ----
    print("\n[4/5] Computing rename propagation ...")
    print("  {}.{}  ->  {}".format(module_name, port_name, new_name))
    plan = compute_rename_plan(modules, parent_map, module_name, port_name, new_name)
    display_plan(plan, modules)

    if not args.dry_run and not args.yes:
        try:
            resp = input("\nApply these changes? [y/N] ").strip().lower()
        except EOFError:
            resp = 'n'
        if resp not in ('y', 'yes'):
            print("Aborted.")
            sys.exit(0)

    # ---- 5. Apply ----
    if args.dry_run:
        print("\n[5/5] Dry-run -- computing diffs ...")
        by_file = defaultdict(list)
        for action in plan:
            by_file[action['file']].append(action)
        for filepath, actions in by_file.items():
            with open(filepath, 'r', encoding='utf-8', errors='replace') as fh:
                original = fh.read()
            content = original
            for action in actions:
                start, end = _find_module_span(content, action['module'])
                if start is None:
                    continue
                mt = content[start:end]
                for ct, op, np_ in action['inst_port_renames']:
                    mt = _rename_inst_port(mt, ct, op, np_)
                for os_, ns_ in action['signal_renames']:
                    mt = _rename_signal(mt, os_, ns_)
                content = content[:start] + mt + content[end:]
            if content != original:
                display_diff(filepath, original, content)
        print("\n[DRY RUN] No files were modified.")
    else:
        print("\n[5/5] Applying changes ...")
        results = apply_plan_to_files(plan, dry_run=False, backup=not args.no_backup)
        for fp in results:
            print("  Modified: {}".format(os.path.relpath(fp)))
            if not args.no_backup:
                print("  Backup:   {}".format(os.path.relpath(fp) + '.bak'))
        print("\n[DONE] Renamed '{}' -> '{}' across {} file(s).".format(
            port_name, new_name, len(results)))


if __name__ == '__main__':
    main()

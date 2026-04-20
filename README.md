# Port Hierarchy Rename Tool (Verilog)

芯片设计中的端口层级自动重命名工具。当需要修改某个底层模块的 port 名称（和/或位宽）时，该脚本会根据 VC (filelist) 文件自动解析设计层级，并将该 port 的名称和位宽变更 **自底向上传播** 到所有相关的父级模块。

## 功能概述

1. **解析 VC 文件** -- 支持 `-f`/`-F` 嵌套引用、`-v` 前缀、`//` 注释
2. **解析 Verilog 源码** -- 提取 module 的 port、wire/reg 声明和实例化关系
3. **构建设计层级** -- 自动发现 parent-child 关系，打印层级树
4. **计算重命名计划** -- 从目标模块出发，BFS 向上追踪端口连接关系
5. **智能声明替换** -- 分别处理声明中的位宽和名称，以及表达式中的信号引用
6. **位宽同步修改** -- 输入 `data_in[7:0]` -> `rx_data[8:0]`，声明处的 `[7:0]` 和 `data_in` 会分别被替换为 `[8:0]` 和 `rx_data`
7. **安全机制** -- dry-run 预览、`.bak` 自动备份、交互式确认

## 环境要求

- Python 3.6+（无第三方依赖）

## 快速开始

### 目录结构

```
.
|-- port_hierarchy_rename.py   # 脚本本体
|-- test_rtl/
|   |-- project.vc             # VC filelist
|   |-- leaf_cell.v            # 叶子模块
|   |-- sub_unit.v             # 中间子单元
|   |-- mid_block.v            # 中间层模块
|   +-- top.v                  # 顶层模块
+-- README.md                  # 本文档
```

### 测试用例层级

```
top
+-- mid_block (u_mid)
    +-- sub_unit (u_sub)
        +-- leaf_cell (u_leaf)
```

每一层都有 `data_in[7:0]` 端口向下穿透。

## 使用方法

### 命令行参数

```
python3 port_hierarchy_rename.py [选项]
```

| 参数 | 说明 | 示例 |
|------|------|------|
| `-m`, `--module` | 要修改 port 的模块名（不含 `.v` 后缀） | `-m leaf_cell` |
| `-p`, `--port` | 当前 port 名称（可包含位宽） | `-p data_in` 或 `-p "data_in[7:0]"` |
| `-n`, `--new-name` | 新的 port 名称（可包含新位宽） | `-n rx_data` 或 `-n "rx_data[8:0]"` |
| `-vc`, `--vc-file` | VC / filelist 文件路径 | `-vc test_rtl/project.vc` |
| `--dry-run` | 只预览变更，不修改文件 | |
| `--no-backup` | 不生成 `.bak` 备份文件 | |
| `-y`, `--yes` | 跳过交互式确认，直接执行 | |
| `--show-hierarchy` | 仅打印层级树后退出 | |

如果省略 `-m`、`-p`、`-n`、`-vc` 中的任何参数，脚本会进入交互模式逐一提示输入。

### 示例 1：仅改名（保持位宽不变）

```bash
python3 port_hierarchy_rename.py \
    -m leaf_cell \
    -p data_in \
    -n rx_data \
    -vc test_rtl/project.vc \
    --dry-run
```

效果：`input [7:0] data_in` -> `input [7:0] rx_data`（位宽 `[7:0]` 不变）

### 示例 2：同时改名和改位宽

```bash
python3 port_hierarchy_rename.py \
    -m leaf_cell \
    -p "data_in[7:0]" \
    -n "rx_data[8:0]" \
    -vc test_rtl/project.vc \
    --dry-run
```

效果：`input [7:0] data_in` -> `input [8:0] rx_data`

脚本会智能地将位宽和名称分开处理：
- 声明中的 `[7:0]` 替换为 `[8:0]`
- 声明和表达式中的 `data_in` 替换为 `rx_data`
- 所有父层级的对应 port 和 wire 声明同步更新

### 示例 3：执行重命名（自动确认）

```bash
python3 port_hierarchy_rename.py \
    -m leaf_cell \
    -p "data_in[7:0]" \
    -n "rx_data[8:0]" \
    -vc test_rtl/project.vc \
    -y
```

执行后：
- `leaf_cell.v`: `input [7:0] data_in` -> `input [8:0] rx_data`，表达式中 `data_in` -> `rx_data`
- `sub_unit.v`: port 声明更新 + 实例连接 `.data_in()` -> `.rx_data()`
- `mid_block.v`: 同上逻辑向上传播
- `top.v`: 同上逻辑向上传播

### 示例 4：查看层级树

```bash
python3 port_hierarchy_rename.py \
    -vc test_rtl/project.vc \
    --show-hierarchy
```

### 示例 5：交互模式

```bash
python3 port_hierarchy_rename.py
```

脚本会依次提示输入：
1. VC 文件路径
2. 模块名称
3. 当前 port 名称（可带位宽如 `data_in[7:0]`）
4. 新的 port 名称（可带新位宽如 `rx_data[8:0]`）

## 位宽处理逻辑

| `-p` 输入 | `-n` 输入 | 声明替换效果 |
|-----------|-----------|-------------|
| `data_in` | `rx_data` | `input [7:0] data_in` -> `input [7:0] rx_data`（仅改名） |
| `data_in[7:0]` | `rx_data[8:0]` | `input [7:0] data_in` -> `input [8:0] rx_data`（改名+改宽） |
| `data_in[7:0]` | `rx_data` | `input [7:0] data_in` -> `input [7:0] rx_data`（仅改名，忽略旧宽） |
| `valid` | `data_valid[3:0]` | `output valid` -> `output [3:0] data_valid`（改名+加宽） |

## 工作原理

```
  VC File  --parse-->  Verilog Files  --parse-->  Module DB
                                                    |
                                          build hierarchy
                                                    |
                                                    v
  target_module.port --BFS upward-->  Rename Plan
                                          |
                                    apply to files
                                          |
                                          v
                                   Modified Sources
```

1. **VC 解析**：读取 filelist，支持递归 `-f` 引用和相对/绝对路径
2. **Verilog 解析**：正则解析 module 定义、ANSI/non-ANSI port 声明、wire/reg、实例化
3. **层级构建**：从实例化关系反向构建 child -> parent 映射
4. **BFS 传播**：从目标 port 出发，沿实例连接关系向上遍历：
   - 找到父模块中实例的 `.port_name(signal)` 连接
   - 重命名 `.port_name` -> `.new_name`
   - 如果 `signal` 是父模块的同名 port，则继续向上传播
5. **智能替换**：
   - 声明行（port/wire/reg）：位宽和名称分别替换
   - 表达式（assign/always）：仅替换信号名
   - 实例连接（`.xxx()`）：仅替换端口连接名

## 注意事项

- 脚本使用正则解析，适用于常见的 RTL 编码风格。对于非常规写法（如宏展开生成的 port）可能需要人工检查
- 如果父模块中的实例连接是表达式（如 `.data_in({a, b})`）而非简单信号名，脚本只会重命名 port 连接名，不会修改表达式内容
- 建议先用 `--dry-run` 预览，确认无误后再执行
- 默认会创建 `.bak` 备份文件，可用 `--no-backup` 关闭
- 支持同一文件中包含多个 module 的情况

## 支持的 VC 文件格式

```
// 注释行
path/to/file.v           // 直接文件路径
-v path/to/file.v        // -v 前缀
-f path/to/another.vc    // 递归引用其他 filelist
-F path/to/another.vc    // 同 -f
+incdir+path/            // 自动忽略
+define+MACRO=1           // 自动忽略
```

## License

MIT

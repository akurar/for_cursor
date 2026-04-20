// ============================================================
//  mid_block — 中间层模块 (Mid-level block)
//  Wraps sub_unit; data_in port passes through directly.
// ============================================================
module mid_block (
    input              clk,
    input              rst_n,
    input       [7:0]  data_in,
    output      [7:0]  data_out,
    output             valid
);

    wire [7:0] data_out_i;
    wire       valid_i;

    sub_unit u_sub (
        .clk      (clk         ),
        .rst_n    (rst_n       ),
        .data_in  (data_in     ),
        .data_out (data_out_i  ),
        .valid    (valid_i     )
    );

    assign data_out = data_out_i;
    assign valid    = valid_i;

endmodule

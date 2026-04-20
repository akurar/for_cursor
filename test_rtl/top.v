// ============================================================
//  top — 顶层模块 (Top-level module)
//  The root of the hierarchy; data_in passes through to mid_block.
// ============================================================
module top (
    input              clk,
    input              rst_n,
    input       [7:0]  data_in,
    output      [7:0]  data_out,
    output             valid
);

    wire [7:0] data_out_top;
    wire       valid_top;

    mid_block u_mid (
        .clk      (clk          ),
        .rst_n    (rst_n        ),
        .data_in  (data_in      ),
        .data_out (data_out_top ),
        .valid    (valid_top    )
    );

    assign data_out = data_out_top;
    assign valid    = valid_top;

endmodule

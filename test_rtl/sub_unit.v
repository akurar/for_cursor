// ============================================================
//  sub_unit — 中间子单元 (Sub-unit)
//  Instantiates leaf_cell; data_in port passes through directly.
// ============================================================
module sub_unit (
    input              clk,
    input              rst_n,
    input       [7:0]  data_in,
    output      [7:0]  data_out,
    output             valid
);

    wire [7:0] data_out_w;
    wire       valid_w;

    leaf_cell u_leaf (
        .clk      (clk        ),
        .rst_n    (rst_n      ),
        .data_in  (data_in    ),
        .data_out (data_out_w ),
        .valid    (valid_w    )
    );

    assign data_out = data_out_w;
    assign valid    = valid_w;

endmodule

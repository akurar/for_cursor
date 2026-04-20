// ============================================================
//  leaf_cell — 叶子模块 (Leaf module)
//  A simple data processing unit at the bottom of the hierarchy.
// ============================================================
module leaf_cell (
    input              clk,
    input              rst_n,
    input       [7:0]  data_in,
    output reg  [7:0]  data_out,
    output             valid
);

    reg valid_r;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            data_out <= 8'b0;
            valid_r  <= 1'b0;
        end else begin
            data_out <= data_in + 8'd1;
            valid_r  <= 1'b1;
        end
    end

    assign valid = valid_r;

endmodule

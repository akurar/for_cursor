module mid (
    input  wire [3:0] d,
    output wire       o
);
    sub s0 (
        .x(d),
        .y(o)
    );
endmodule

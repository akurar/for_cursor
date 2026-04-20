module top (
    input  wire [3:0] d,
    output wire       o
);
    mid m0 (
        .d(d),
        .o(o)
    );
endmodule

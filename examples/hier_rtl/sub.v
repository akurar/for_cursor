module sub (
    input  wire [3:0] x,
    output wire       y
);
    wire [3:0] bus;

    leaf u0 (
        .in_a(bus),
        .out_b(y)
    );

    assign bus = x;
endmodule

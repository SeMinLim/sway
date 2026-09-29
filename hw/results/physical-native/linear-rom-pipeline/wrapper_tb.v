module pair #(parameter WORDS=1, WIDTH=8, ABITS=13, PIPELINED=1, INDEX=0, FILE="", BINARY=0) (input clk);
reg en=0, we=0; reg [ABITS-1:0] addr=0; reg [WIDTH-1:0] di=0;
wire [WIDTH-1:0] actual, expected;
BRAM1Load #(.FILENAME(FILE), .PIPELINED(PIPELINED), .ADDR_WIDTH(ABITS), .DATA_WIDTH(WIDTH), .MEMSIZE(WORDS), .BINARY(BINARY)) dut(clk,en,we,addr,di,actual);
BRAM1LoadReference #(.FILENAME(FILE), .PIPELINED(PIPELINED), .ADDR_WIDTH(ABITS), .DATA_WIDTH(WIDTH), .MEMSIZE(WORDS), .BINARY(BINARY)) refmem(clk,en,we,addr,di,expected);
integer cycle=0;
always @(negedge clk) begin
 if (cycle > 4 && actual !== expected) begin
  $display("WRAPPER_FAIL index=%0d pipelined=%0d cycle=%0d addr=%0d actual=%h expected=%h",INDEX,PIPELINED,cycle,addr,actual,expected); $fatal;
 end
 cycle=cycle+1;
 en = cycle < 24 || ($random & 3) != 0;
 we = cycle >= 24 && ($random & 7) == 0;
 case (cycle % 19)
  0: addr=0; 1: addr=WORDS-1; 2: addr=2047 % WORDS;
  3: addr=2048 % WORDS; 4: addr=4095 % WORDS; 5: addr=4096 % WORDS;
  6: addr=6143 % WORDS; 7: addr=6144 % WORDS;
  default: addr=($random & 32'h7fffffff) % WORDS;
 endcase
 di=$random;
end
endmodule
module tb;
reg clk=0; always #5 clk=~clk;
pair #(.WORDS(6400), .WIDTH(8), .ABITS(13), .PIPELINED(0), .INDEX(0), .FILE("rom0.hex")) p0_0(clk);
pair #(.WORDS(6400), .WIDTH(8), .ABITS(13), .PIPELINED(1), .INDEX(0), .FILE("rom0.hex")) p0_1(clk);
pair #(.WORDS(3200), .WIDTH(8), .ABITS(13), .PIPELINED(0), .INDEX(1), .FILE("rom1.hex")) p1_0(clk);
pair #(.WORDS(3200), .WIDTH(8), .ABITS(13), .PIPELINED(1), .INDEX(1), .FILE("rom1.hex")) p1_1(clk);
pair #(.WORDS(1600), .WIDTH(8), .ABITS(13), .PIPELINED(0), .INDEX(2), .FILE("rom2.hex")) p2_0(clk);
pair #(.WORDS(1600), .WIDTH(8), .ABITS(13), .PIPELINED(1), .INDEX(2), .FILE("rom2.hex")) p2_1(clk);
pair #(.WORDS(2049), .WIDTH(8), .ABITS(13), .PIPELINED(0), .INDEX(3), .FILE("rom3.hex")) p3_0(clk);
pair #(.WORDS(2049), .WIDTH(8), .ABITS(13), .PIPELINED(1), .INDEX(3), .FILE("rom3.hex")) p3_1(clk);
pair #(.WORDS(17), .WIDTH(1), .ABITS(5), .PIPELINED(0), .INDEX(4), .FILE("rom4.hex")) p4_0(clk);
pair #(.WORDS(17), .WIDTH(1), .ABITS(5), .PIPELINED(1), .INDEX(4), .FILE("rom4.hex")) p4_1(clk);
pair #(.WORDS(4097), .WIDTH(9), .ABITS(13), .PIPELINED(0), .INDEX(5), .FILE("rom5.hex")) p5_0(clk);
pair #(.WORDS(4097), .WIDTH(9), .ABITS(13), .PIPELINED(1), .INDEX(5), .FILE("rom5.hex")) p5_1(clk);
pair #(.WORDS(3073), .WIDTH(16), .ABITS(12), .PIPELINED(0), .INDEX(6), .FILE("rom6.hex")) p6_0(clk);
pair #(.WORDS(3073), .WIDTH(16), .ABITS(12), .PIPELINED(1), .INDEX(6), .FILE("rom6.hex")) p6_1(clk);
pair #(.WORDS(1), .WIDTH(8), .ABITS(1), .PIPELINED(0), .INDEX(7), .FILE("rom7.hex")) p7_0(clk);
pair #(.WORDS(1), .WIDTH(8), .ABITS(1), .PIPELINED(1), .INDEX(7), .FILE("rom7.hex")) p7_1(clk);
pair #(.WORDS(6400), .WIDTH(8), .ABITS(13), .PIPELINED(1), .INDEX(0), .FILE("rom0.bin"), .BINARY(1)) binary_case(clk);
initial begin #50000; $display("WRAPPER_PASS pairs=17 cycles=5000 comparisons=84915"); $finish; end
endmodule

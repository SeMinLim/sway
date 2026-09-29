from pathlib import Path
import argparse, json, random, subprocess, hashlib, resource
parser=argparse.ArgumentParser(description="Compare the banked wrapper with the prior BRAM behavior.")
parser.add_argument('--rtl',type=Path,required=True)
parser.add_argument('--reference',type=Path,default=Path(__file__).resolve().parent/'BRAM1Load.before.v')
parser.add_argument('--output',type=Path,required=True)
parser.add_argument('--iverilog',default='iverilog')
parser.add_argument('--vvp',default='vvp')
parser.add_argument('--ivl-dir',type=Path)
args=parser.parse_args()
root=args.output.resolve();root.mkdir(parents=True,exist_ok=True)
rtl=args.rtl.resolve()
reference=args.reference.resolve()
(root/'BRAM1Load.reference.v').write_text(reference.read_text().replace('module BRAM1Load(', 'module BRAM1LoadReference('))
cases=[(6400,8,13),(3200,8,13),(1600,8,13),(2049,8,13),(17,1,5),(4097,9,13),(3073,16,12),(1,8,1)]
for i,(words,width,abits) in enumerate(cases):
 r=random.Random(9120+i)
 values=[r.randrange(1<<width) for _ in range(words)]
 (root/f'rom{i}.hex').write_text(''.join(f'{v:x}\n' for v in values))
 (root/f'rom{i}.bin').write_text(''.join(f'{v:0{width}b}\n' for v in values))
test='''module pair #(parameter WORDS=1, WIDTH=8, ABITS=13, PIPELINED=1, INDEX=0, FILE="", BINARY=0) (input clk);
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
'''
for i,(words,width,abits) in enumerate(cases):
 for pipeline in [0,1]:
  test+=f'pair #(.WORDS({words}), .WIDTH({width}), .ABITS({abits}), .PIPELINED({pipeline}), .INDEX({i}), .FILE("rom{i}.hex")) p{i}_{pipeline}(clk);\n'
test+='pair #(.WORDS(6400), .WIDTH(8), .ABITS(13), .PIPELINED(1), .INDEX(0), .FILE("rom0.bin"), .BINARY(1)) binary_case(clk);\n'
test+='initial begin #50000; $display("WRAPPER_PASS pairs=17 cycles=5000"); $finish; end\nendmodule\n'
(root/'wrapper_tb.v').write_text(test)
cmd=[args.iverilog,*(['-B',str(args.ivl_dir)] if args.ivl_dir else []),'-g2012','-s','tb','-o','wrapper.vvp',str(rtl),'BRAM1Load.reference.v','wrapper_tb.v']
subprocess.run(cmd,cwd=root,check=True)
r=subprocess.run([args.vvp,'wrapper.vvp'],cwd=root,text=True,capture_output=True,check=True)
(root/'wrapper.log').write_text(r.stdout+r.stderr); print(r.stdout)
assert 'WRAPPER_FAIL' not in r.stdout and 'WRAPPER_PASS' in r.stdout
(root/'wrapper-report.json').write_text(json.dumps({'status':'PASS','cases':cases,'pipelined':[False,True],'binary_init_cases':1,'cycles_per_case':5000,'command':cmd,'sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [rtl,reference,root/'wrapper_tb.v',root/'wrapper.log']}},indent=2)+'\n')
(root/'rom_top.v').write_text('module rom_top(input CLK, EN, input [12:0] ADDR, output [7:0] DO); BRAM1Load #(.FILENAME("rom0.hex"), .PIPELINED(1), .ADDR_WIDTH(13), .DATA_WIDTH(8), .MEMSIZE(6400)) memory(CLK, EN, 1\'b0, ADDR, 8\'b0, DO); endmodule\n')

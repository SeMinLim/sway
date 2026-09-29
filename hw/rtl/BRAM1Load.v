/*
Copyright (c) 2020 Bluespec, Inc. All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are
met:

1. Redistributions of source code must retain the above copyright
   notice, this list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright
   notice, this list of conditions and the following disclaimer in the
   documentation and/or other materials provided with the
   distribution.

3. Neither the name of the copyright holder nor the names of its
   contributors may be used to endorse or promote products derived
   from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
"AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
*/

// BSC 2026.01 BRAM1Load.v, with block RAM inference for Sway affine ROMs.
// Skip the unspecialized empty filename when Yosys eagerly parses the library.

`ifdef BSV_ASSIGNMENT_DELAY
`else
 `define BSV_ASSIGNMENT_DELAY
`endif

// Single-Ported BRAM
module BRAM1Load(CLK,
                 EN,
                 WE,
                 ADDR,
                 DI,
                 DO
                 );

   parameter                      FILENAME   = "";
   parameter                      PIPELINED  = 0;
   parameter                      ADDR_WIDTH = 1;
   parameter                      DATA_WIDTH = 1;
   parameter                      MEMSIZE    = 1;
   parameter                      BINARY     = 0;

   input                          CLK;
   input                          EN;
   input                          WE;
   input [ADDR_WIDTH-1:0]         ADDR;
   input [DATA_WIDTH-1:0]         DI;
   output [DATA_WIDTH-1:0]        DO;

   genvar bank;
   generate
      if (PIPELINED) begin : pipelined
         // ECP5 stores at most 2048 eight-bit words per EBR. Register each
         // bank's output before the bank mux, rather than the combined mux.
         localparam BANK_ADDR_WIDTH = (ADDR_WIDTH < 11) ? ADDR_WIDTH : 11;
         localparam BANK_SIZE = 1 << BANK_ADDR_WIDTH;
         localparam BANK_COUNT = (MEMSIZE + BANK_SIZE - 1) / BANK_SIZE;
         localparam BANK_SELECT_WIDTH = (ADDR_WIDTH > BANK_ADDR_WIDTH) ?
                                       ADDR_WIDTH - BANK_ADDR_WIDTH : 1;
         wire [DATA_WIDTH-1:0] bankOutput [0:BANK_COUNT-1];
         reg [BANK_SELECT_WIDTH-1:0] bankR;
         reg [BANK_SELECT_WIDTH-1:0] bankR2;

         // synopsys translate_off
         initial begin
`ifndef BSV_NO_INITIAL_BLOCKS
            bankR = 0;
            bankR2 = 0;
`endif
         end
         // synopsys translate_on

         always @(posedge CLK) begin
            if (EN)
               bankR <= `BSV_ASSIGNMENT_DELAY ADDR >> BANK_ADDR_WIDTH;
            bankR2 <= `BSV_ASSIGNMENT_DELAY bankR;
         end

         for (bank = 0; bank < BANK_COUNT; bank = bank + 1) begin : banks
            localparam WORDS = (MEMSIZE - bank * BANK_SIZE < BANK_SIZE) ?
                              MEMSIZE - bank * BANK_SIZE : BANK_SIZE;
            (* ram_style = "block" *) reg [DATA_WIDTH-1:0] RAM [0:WORDS-1];
            (* mem2reg *) reg [DATA_WIDTH-1:0] initialImage [0:MEMSIZE-1];
            reg [DATA_WIDTH-1:0] dataR;
            reg [DATA_WIDTH-1:0] outputR;
            integer word;

            // The initial-only image resolves to constants before bank RAM inference.
            initial begin
               if (FILENAME != "") begin
                  if (BINARY)
                     $readmemb(FILENAME, initialImage, 0, MEMSIZE-1);
                  else
                     $readmemh(FILENAME, initialImage, 0, MEMSIZE-1);
                  for (word = 0; word < WORDS; word = word + 1)
                     RAM[word] = initialImage[bank * BANK_SIZE + word];
               end
            end

            // synopsys translate_off
            initial begin
`ifndef BSV_NO_INITIAL_BLOCKS
               dataR = { ((DATA_WIDTH+1)/2) { 2'b10 } };
               outputR = { ((DATA_WIDTH+1)/2) { 2'b10 } };
`endif
            end
            // synopsys translate_on

            always @(posedge CLK) begin
               if (EN) begin
                  if (WE && (ADDR >> BANK_ADDR_WIDTH) == bank) begin
                     RAM[ADDR[BANK_ADDR_WIDTH-1:0]] <= `BSV_ASSIGNMENT_DELAY DI;
                     dataR <= `BSV_ASSIGNMENT_DELAY DI;
                  end else begin
                     dataR <= `BSV_ASSIGNMENT_DELAY RAM[ADDR[BANK_ADDR_WIDTH-1:0]];
                  end
               end
               outputR <= `BSV_ASSIGNMENT_DELAY dataR;
            end
            assign bankOutput[bank] = outputR;
         end
         assign DO = bankOutput[bankR2];
      end else begin : unpipelined
      (* ram_style = "block" *) reg [DATA_WIDTH-1:0] RAM[0:MEMSIZE-1];
      reg [DATA_WIDTH-1:0]           DO_R;
      reg [DATA_WIDTH-1:0]           DO_R2;

      // synopsys translate_off
      initial
      begin : init_block
   `ifdef BSV_NO_INITIAL_BLOCKS
   `else
         DO_R  = { ((DATA_WIDTH+1)/2) { 2'b10 } };
         DO_R2 = { ((DATA_WIDTH+1)/2) { 2'b10 } };
   `endif // !`ifdef BSV_NO_INITIAL_BLOCKS
      end
      // synopsys translate_on

      initial
      begin : init_rom_block
         if (FILENAME != "") begin
            if (BINARY)
              $readmemb(FILENAME, RAM, 0, MEMSIZE-1);
            else
              $readmemh(FILENAME, RAM, 0, MEMSIZE-1);
         end
      end

      always @(posedge CLK) begin
         if (EN) begin
            if (WE) begin
               RAM[ADDR] <= `BSV_ASSIGNMENT_DELAY DI;
               DO_R <= `BSV_ASSIGNMENT_DELAY DI;
            end
            else begin
               DO_R <= `BSV_ASSIGNMENT_DELAY RAM[ADDR];
            end
         end
         DO_R2 <= `BSV_ASSIGNMENT_DELAY DO_R;
      end

      // Output driver
      assign DO = (PIPELINED) ? DO_R2 : DO_R;
      end
   endgenerate

endmodule // BRAM1Load

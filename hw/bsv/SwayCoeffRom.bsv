package SwayCoeffRom;
import FIFO::*;
import RegFile::*;
import RWire::*;
import SwayParameters::*;

interface SwayCoeffRawIfc;
	method Action request(Bit#(11) address);
	method Int#(8) data;
endinterface

import "BVI" SwayCoeffRom =
module mkSwayCoeffRawNative#(Integer bankId)(SwayCoeffRawIfc);
	parameter BANK_ID = bankId;
	default_clock clk(CLK);
	default_reset no_reset;
	method request(ADDR) enable(REQ);
	method DATA data;
	schedule request C request;
	schedule data CF (request, data);
endmodule

module mkSwayCoeffRawModel#(String filename, Integer depth)(SwayCoeffRawIfc);
	RegFile#(Bit#(11), Int#(8)) memory <- mkRegFileLoad(filename, 0, fromInteger(depth - 1));
	Wire#(Bit#(11)) addressW <- mkDWire(0);
	Reg#(Int#(8)) rawR <- mkRegU;
	Reg#(Int#(8)) outputR <- mkRegU;
	rule process1;
		rawR <= memory.sub(addressW);
		outputR <= rawR;
	endrule
	method Action request(Bit#(11) address);
		addressW <= address;
	endmethod
	method Int#(8) data = outputR;
endmodule

interface SwayCoeffRomIfc;
	method Action request(Bit#(11) address);
	method ActionValue#(Int#(8)) response;
endinterface

// Four credits reserve output slots for all outstanding requests, including
// two memory stages. Native CE/OCE stay high. Validity resets independently.
module mkSwayCoeffRom#(Integer layerId, Integer lane)(SwayCoeffRomIfc);
`ifdef SWAY_ROM_NATIVE
	SwayCoeffRawIfc raw <- mkSwayCoeffRawNative(layerId * 4 + lane);
`elsif BSIM
	SwayCoeffRawIfc raw <- mkSwayCoeffRawModel(linearRomPath(layerId,lane), layerBankDepth(layerId));
`else
	SwayCoeffRawIfc raw <- mkSwayCoeffRawNative(layerId * 4 + lane);
`endif
	FIFO#(Int#(8)) outputQ <- mkSizedFIFO(4);
	PulseWire requestOn <- mkPulseWire;
	Reg#(Bool) valid0R <- mkReg(False);
	Reg#(Bool) valid1R <- mkReg(False);
	Reg#(Bit#(3)) issuedCnt <- mkReg(0);
	Reg#(Bit#(3)) returnedCnt <- mkReg(0);
	rule process1;
		valid0R <= requestOn;
		valid1R <= valid0R;
	endrule
	rule process2 ( valid1R );
		outputQ.enq(raw.data);
	endrule
	method Action request(Bit#(11) address) if ( issuedCnt - returnedCnt < 4 );
		raw.request(address);
		requestOn.send;
		issuedCnt <= issuedCnt + 1;
	endmethod
	method ActionValue#(Int#(8)) response;
		let result = outputQ.first;
		outputQ.deq;
		returnedCnt <= returnedCnt + 1;
		return result;
	endmethod
endmodule
endpackage

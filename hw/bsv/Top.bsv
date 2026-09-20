package Top;

import Clocks::*;
import Uart::*;
import SwayClock::*;

import SwayTypes::*;
import SwayBaseline::*;
import SwayReset::*;

interface SwayTopIfc;
	(* always_ready *) method Bit#(1) ftdi_rxd;
	(* always_enabled, always_ready, prefix="", result="serial_txd" *)
	method Action ftdi_tx(Bit#(1) ftdi_txd);
	(* always_ready, prefix="", result="led" *) method Bit#(8) led;
endinterface

// ULX3S transport: raw 320 signed INT8 input bytes, then 57 output bytes.
// Host sends one complete frame and reads its reply before sending another.
// UART serialization is outside the accelerator kernel cycle measurement.
(* no_default_clock, no_default_reset *)
module mkTop#(Clock clk_25mhz)(SwayTopIfc);
	SwayClockIfc clocks <- mkSwayClock(clk_25mhz);
	LocalResetIfc txReset <- mkSwayLocalReset(clocked_by clocks.clk100, reset_by clocks.rst100);
	UartIfc uart <- mkUart(217, clocked_by clk_25mhz, reset_by clocks.rst25);
	SyncFIFOIfc#(Bit#(8)) rxQ <- mkSyncFIFO(16, clk_25mhz, clocks.rst25, clocks.clk100);
	SyncFIFOIfc#(Bit#(8)) txQ <- mkSyncFIFO(16, clocks.clk100, txReset.rst, clk_25mhz);
	SwayIfc core <- mkSwayBaseline(clocked_by clocks.clk100, reset_by clocks.rst100);

	rule uartInput;
		let value <- uart.user.get;
		rxQ.enq(value);
	endrule

	rule process1;
		core.put(unpack(rxQ.first));
		rxQ.deq;
	endrule

	rule process2 ( txReset.ready );
		let value <- core.get;
		txQ.enq(pack(value));
	endrule

	rule uartOutput;
		uart.user.send(txQ.first);
		txQ.deq;
	endrule

	method Bit#(1) ftdi_rxd = uart.serial_txd;
	method Action ftdi_tx(Bit#(1) ftdi_txd);
		uart.serial_rx(ftdi_txd);
	endmethod
	method Bit#(8) led = 8'h01;
endmodule

endpackage

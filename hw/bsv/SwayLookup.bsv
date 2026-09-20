package SwayLookup;

import Vector::*;

import SwayParameters::*;

// Each candidate depends only on the low nibble. Register the candidates before
// selecting with the high nibble to bound the nonlinear lookup combinational depth.
function Vector#(16, Int#(8)) nonlinearCandidates(Integer tableId, Bit#(4) low);
	Vector#(16, Int#(8)) values = newVector;
	for ( Integer high = 0; high < 16; high = high + 1 ) begin
		Bit#(8) address = {fromInteger(high), low};
		values[high] = nonlinearLookup(tableId, unpack(address));
	end
	return values;
endfunction

endpackage

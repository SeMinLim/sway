import { readFileSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { join } from "node:path";

function convertAndCheck(source) {
  const original = source.match(/function Int#\(8\) linearBias\(Integer layerId, Bit#\(9\) row\);[\s\S]*?endfunction/);
  if (!original) throw new Error("linearBias function not found");
  const tables = [];
  for (const match of original[0].matchAll(/\tif \( layerId == (\d+) \) begin\n\t\tcase \( row \)\n([\s\S]*?)\t\tendcase\n\tend/g)) {
    const layer = Number(match[1]), values = Array(512).fill(0);
    for (const entry of match[2].matchAll(/9'd(\d+): result = (-?\d+);/g)) {
      const row = Number(entry[1]), value = Number(entry[2]);
      if (row < 0 || row >= 512 || value < -128 || value > 127) throw new Error("Invalid coefficient");
      values[row] = value;
    }
    if (!match[2].includes("default: result = 0;")) throw new Error("Unexpected default");
    tables.push({layer, values});
  }
  if (tables.map(t => t.layer).join(",") !== "0,3,7,9,10") throw new Error("Unexpected layer set");
  const lines = ["function Int#(8) linearBias(Integer layerId, Bit#(9) row);", "\tBit#(8) result = 0;"];
  for (const {layer, values} of tables) {
    lines.push("\tif ( layerId == " + layer + " ) begin");
    for (let bit = 0; bit < 8; bit++) {
      let mask = 0n;
      for (let row = 0; row < 512; row++)
        mask |= BigInt(((values[row] & 255) >>> bit) & 1) << BigInt(row);
      lines.push("\t\tBit#(512) truth" + bit + " = 512'h" + mask.toString(16).padStart(128, "0") + ";");
      lines.push("\t\tresult[" + bit + "] = truth" + bit + "[row];");
    }
    lines.push("\tend");
  }
  lines.push("\treturn unpack(result);", "endfunction");
  const replacement = lines.join("\n");
  const output = source.replace(original[0], replacement);
  const originalTables = new Map(tables.map(t => [t.layer, t.values]));
  const planes = new Map();
  for (const match of replacement.matchAll(/\tif \( layerId == (\d+) \) begin\n([\s\S]*?)\tend/g)) {
    const masks = Array.from(match[2].matchAll(/Bit#\(512\) truth(\d+) = 512'h([0-9a-f]{128});/g));
    if (masks.length !== 8 || masks.some((m, i) => Number(m[1]) !== i)) throw new Error("Malformed generated planes");
    planes.set(Number(match[1]), masks.map(m => BigInt("0x" + m[2])));
  }
  const unknownLayers = [-1, 11, 999];
  let normalCases = 0, unknownCases = 0;
  for (const layer of [...Array(11).keys(), ...unknownLayers]) {
    for (let row = 0; row < 512; row++) {
      const expected = originalTables.get(layer)?.[row] ?? 0;
      let encoded = 0;
      for (let bit = 0; bit < 8; bit++)
        encoded |= Number(((planes.get(layer)?.[bit] ?? 0n) >> BigInt(row)) & 1n) << bit;
      const actual = encoded >= 128 ? encoded - 256 : encoded;
      if (actual !== expected) throw new Error("Mismatch at layer=" + layer + ", row=" + row);
      if (layer >= 0 && layer < 11) normalCases++; else unknownCases++;
    }
  }
  const withoutOld = source.replace(original[0], ""), withoutNew = output.replace(replacement, "");
  if (withoutOld !== withoutNew) throw new Error("Unrelated source changed");
  return {source: output, proof: {status: "pass", method: "Enumerated the former signed case-table values against independently decoded emitted constant truth-plane bits", layers: 11, row_addresses_per_layer: 512, lookup_cases: normalCases, unknown_layers: unknownLayers, unknown_layer_cases: unknownCases, bias_layers: tables.map(t => t.layer), other_parameter_source_bytes_unchanged: true, affine_rom_files_changed: false}};
}

// Reproduce from the previous committed parameter file without running the model:
// git show 69e5106c7c8256ba7d54bc9deceb06db4ded6a6f:hw/generated/SwayParameters.bsv > /tmp/SwayParameters.before.bsv
// node convert-and-check.mjs /tmp/SwayParameters.before.bsv /tmp/SwayParameters.after.bsv hw/model/export
if (process.argv.length !== 5) throw new Error("Usage: node convert-and-check.mjs BEFORE_BSV AFTER_BSV EXPORT_DIRECTORY");
const before = readFileSync(process.argv[2], "utf8");
const digest = data => createHash("sha256").update(data).digest("hex");
if (digest(before) !== "cfa5f1526f37ae09740986e4cccd92a7facd0686cd27eca784c88f4d721dac45") throw new Error("Unexpected source");
const result = convertAndCheck(before);
if (digest(result.source) !== "a57d31c5bcf3fd406bb099103995a6d6c276c981c2aad92c2d7099ec6f00328d") throw new Error("Unexpected converted source");
const biasFunction = result.source.match(/function Int#\(8\) linearBias[\s\S]*?endfunction/)[0];
const manifest = JSON.parse(readFileSync(join(process.argv[4], "manifest.json"), "utf8"));
const names = [[0, "embedding_bias"], [3, "blocks_0_dtBias"], [7, "blocks_1_dtBias"], [9, "headHidden_bias"], [10, "headOutput_bias"]];
let trainedValues = 0;
for (const [layer, name] of names) {
  const tensor = manifest.tensors.find(t => t.name === name);
  const bytes = Buffer.from(readFileSync(join(process.argv[4], tensor.hex), "utf8").trim().split(/\s+/).map(v => parseInt(v, 16)));
  if (bytes.length !== tensor.elements || digest(bytes) !== tensor.sha256) throw new Error("Frozen tensor mismatch: " + name);
  const body = biasFunction.match(new RegExp("\\tif \\( layerId == " + layer + " \\) begin\\n([\\s\\S]*?)\\tend"))[1];
  const planes = Array.from(body.matchAll(/512'h([0-9a-f]{128})/g)).map(m => BigInt("0x" + m[1]));
  for (let row = 0; row < bytes.length; row++) {
    let value = 0;
    for (let bit = 0; bit < 8; bit++) value |= Number((planes[bit] >> BigInt(row)) & 1n) << bit;
    if (value !== bytes[row]) throw new Error("Generated bias mismatch: " + name);
    trainedValues++;
  }
}
writeFileSync(process.argv[3], result.source);
console.log(JSON.stringify({...result.proof, frozen_export_values_checked: trainedValues, input_sha256: digest(before), output_sha256: digest(result.source)}, null, 2));

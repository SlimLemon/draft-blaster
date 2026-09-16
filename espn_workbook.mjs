// Isolated XLSX renderer. Receives normalized JSON; never loads ESPN credentials.
import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
const require = createRequire(import.meta.url);
const entry = require.resolve("@oai/artifact-tool", {
  paths: [process.env.DRAFT_COPILOT_ARTIFACT_MODULES || path.dirname(process.execPath)],
});
const { Workbook, SpreadsheetFile } = await import(pathToFileURL(entry).href);
const [source, target, previewFlag] = process.argv.slice(2);
if (!source || !target) throw new Error("Usage: espn_workbook.mjs snapshot.json output.xlsx [--previews]");
const data = JSON.parse(await fs.readFile(source, "utf8"));
const wb = Workbook.create();
const navy = "#244867";
const specs = [
  ["Draft Picks", data.draft_picks, ["pick_id", "player_id", "player_name", "player_name_source", "bid_amount", "team_id", "team_name"]],
  ["Teams", data.teams, ["team_id", "team_name", "team_token", "projected_rank", "points"]],
  ["Rosters", data.rosters, ["team_id", "team_name", "player_id", "player_name", "player_name_source", "lineup_slot_id", "points"]],
  ["Schedule", data.schedule, ["id", "matchupPeriodId", "winner"]],
  ["Transactions", data.transactions, ["id", "type", "status"]],
  ["Pending Transactions", data.pending_transactions, ["id", "type", "status"]],
  ["Members", data.members, ["id", "displayName"]],
  ["Settings", Object.entries(data.settings).map(([key, value]) => ({key, value})), ["key", "value"]],
  ["Status", Object.entries(data.status).map(([key, value]) => ({key, value})), ["key", "value"]],
  ["Draft Detail", Object.entries(data.draft_detail).map(([key, value]) => ({key, value})), ["key", "value"]],
];
const dash = wb.worksheets.add("Dashboard");
const layouts = [];
const oversized = [];
function col(n) { let s=""; for(n++; n; n=Math.floor((n-1)/26)) s=String.fromCharCode(65+(n-1)%26)+s; return s; }
function value(v, pointer) {
  if (v === undefined || v === null) return null;
  if (typeof v === "object") v = JSON.stringify(v);
  if (typeof v === "string") {
    if (v.length > 32000) {
      oversized.push(pointer);
      return "Full value in league_snapshot.json: " + pointer;
    }
    // Explicit values plus quoting stop source text from becoming formulas.
    if (v.startsWith("=")) return "'" + v;
  }
  return v;
}
function styleSheet(sheet, end, width=20) {
  sheet.showGridLines = false;
  sheet.getRange("A1:"+end).format.font = {name:"Arial", size:10, color:"#182433"};
  sheet.getRange("A1:"+end).format.columnWidth = width;
  sheet.getRange("A1:"+end).format.rowHeight = 23;
  sheet.getRange("A1:"+end).format.verticalAlignment = "center";
}
const rowCounts = {};
for (const [name, records, preferred] of specs) {
  const sheet = wb.worksheets.add(name);
  const rows = records || [];
  const keys = [...preferred, ...new Set(rows.flatMap(r=>Object.keys(r)).filter(k=>!preferred.includes(k)))];
  const endCol = col(keys.length-1);
  styleSheet(sheet, endCol+Math.max(6, rows.length+4));
  sheet.getRange("A2").values = [[name]];
  sheet.getRange("A2").format.font = {name:"Arial", size:14, bold:true};
  const state = records === null ? "Unavailable: field omitted by ESPN" : rows.length + " source records";
  sheet.getRange("A3").values = [[state]];
  sheet.getRange("A4:"+endCol+"4").values = [keys];
  sheet.getRange("A4:"+endCol+"4").format = {
    fill:navy, font:{name:"Arial",size:10,bold:true,color:"#FFFFFF"},
    wrapText:true, rowHeight:40, horizontalAlignment:"center", verticalAlignment:"center",
  };
  if(rows.length) {
    sheet.getRange("A5:"+endCol+(rows.length+4)).values =
      rows.map((r,i)=>keys.map(k=>value(r[k], name+"/"+i+"/"+k)));
    sheet.tables.add("A4:"+endCol+(rows.length+4),true,name.replace(/[^a-zA-Z]/g,"")+"Data");
  }
  keys.forEach((k,i)=>{
    const range=sheet.getRange(col(i)+"4:"+col(i)+Math.max(5,rows.length+4));
    if(k.includes("name") || k === "displayName" || k === "key") range.format.columnWidth=34;
    if(k.includes("source") || k === "raw" || k === "value") range.format.columnWidth=48;
    if(k.endsWith("_id") || k==="id") range.setNumberFormat("0");
    if(k==="bid_amount") range.setNumberFormat('"$"#,##0');
    if(k==="points") range.setNumberFormat("0.0");
  });
  sheet.freezePanes.freezeRows(4);
  rowCounts[name] = records === null ? null : rows.length;
  layouts.push([name, "A1:"+col(Math.min(keys.length-1,6))+Math.min(rows.length+4,12)]);
}
styleSheet(dash,"H35",18);
dash.tabColor = navy;
dash.getRange("B2").values = [["ESPN league snapshot"]];
dash.getRange("B2").format.font = {name:"Arial",size:16,bold:true};
dash.getRange("B3").values = [[value(data.settings.name,"settings/name")]];
dash.getRange("B3").format.font = {name:"Arial",size:12};
dash.getRange("B5:C5").values = [["Collection","Records"]];
dash.getRange("B5:C5").format = {fill:navy,font:{name:"Arial",bold:true,color:"#FFFFFF"}};
const summary = Object.entries(rowCounts);
dash.getRange("B6:C"+(5+summary.length)).values =
  summary.map(([name,count])=>[name,count===null?"Unavailable":count]);
dash.getRange("B6:B16").format.columnWidth = 28;
dash.getRange("C6:C16").format.columnWidth = 20;
dash.getRange("E5:G5").merge();
dash.getRange("E5").values = [["Player name coverage"]];
dash.getRange("E5:G5").format = {fill:navy,font:{name:"Arial",bold:true,color:"#FFFFFF"}};
dash.getRange("E7:F9").values = [
  ["Unresolved players",data.unresolved_player_ids.length],
  ["Unresolved draft picks",data.metadata.unresolved_draft_pick_count],
  ["Catalog refresh",data.metadata.player_refresh_error?"Failed; see source details":"Completed"],
];
dash.getRange("E5:E16").format.columnWidth = 29;
dash.getRange("F5:F16").format.columnWidth = 25;
dash.getRange("B19:H21").merge();
dash.getRange("B19").values = [[
  "Names: fresh_catalog uses this refresh; payload uses the league response; cache_fallback uses a dated cache entry. Unknown names remain blank with their ESPN ID."
]];
dash.getRange("B19:H21").format.wrapText = true;
dash.getRange("B23:H25").merge();
dash.getRange("B23").values = [[
  "Snapshot of ESPN fields as returned. Missing collections are unavailable. Nested values longer than Excel's cell limit are referenced in the JSON sidecar."
]];
dash.getRange("B23:H25").format.wrapText = true;
const meta = wb.worksheets.add("Data & Targets");
const metaRows = Object.entries({...data.metadata, ...Object.fromEntries(
  Object.entries(data.coverage).map(([k,v])=>["coverage_"+k,v])),
  oversized_cell_count:oversized.length,
  source:"ESPN private league API",
  nested_values:"Long cells point to league_snapshot.json; raw API data is in league_snapshot_raw.json.",
});
styleSheet(meta,"B"+(metaRows.length+4));
meta.getRange("A2").values=[["Source metadata"]];
meta.getRange("A2").format.font={name:"Arial",size:14,bold:true};
meta.getRange("A4:B4").values=[["Field","Value"]];
meta.getRange("A4:B4").format={fill:navy,font:{bold:true,color:"#FFFFFF"}};
meta.getRange("A5:B"+(metaRows.length+4)).values=metaRows.map(([k,v])=>[k,value(v,"metadata/"+k)]);
meta.getRange("A1:A"+(metaRows.length+4)).format.columnWidth=32;
meta.getRange("B1:B"+(metaRows.length+4)).format.columnWidth=95;
meta.getRange("B5:B"+(metaRows.length+4)).format.wrapText=true;
meta.getRange("A5:B"+(metaRows.length+4)).format.rowHeight=40;
layouts.push(["Data & Targets","A1:B12"],["Dashboard","A1:H26"]);
wb.recalculate();
const validation = await wb.inspect({kind:"table",range:"Dashboard!B5:F15",include:"values,formulas",maxChars:3000,tableMaxRows:11,tableMaxCols:5});
const file = await SpreadsheetFile.exportXlsx(wb);
await file.save(target);
await fs.writeFile(path.join(path.dirname(target),"validation.json"), JSON.stringify({
  rows:rowCounts, unresolved:data.unresolved_player_ids, oversized_cells:oversized, dashboard:validation.ndjson
}, null,2));
if(previewFlag==="--previews") {
  for(const [name,range] of layouts) {
    const blob = await wb.render({sheetName:name,range,scale:1,format:"png"});
    await fs.writeFile(path.join(path.dirname(target), name.replace(/[^a-zA-Z]/g,"")+".png"),
                      new Uint8Array(await blob.arrayBuffer()));
  }
}


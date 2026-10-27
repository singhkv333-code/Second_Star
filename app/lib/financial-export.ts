import { getStatement, type StatementResponse, type StatementType } from "@/lib/api";
import { isError } from "@/lib/types";

export const STATEMENT_LABELS: Record<StatementType, string> = {
  balance_sheet: "Balance sheet", profit_loss: "Profit and loss", cash_flow: "Cash flow", ratios: "Ratios",
};
export type FinancialExport = { symbol: string; statements: StatementResponse[] };
const PROVIDER = "Pivot Tech";
const RIGHTS_NOTICE = "Pivot Tech owns its original software, analysis and report presentation. Underlying market and company data remain subject to applicable third-party rights and licences. Use and redistribution are subject to Pivot's terms and applicable data rights.";
const USE_NOTICE = "For information and research only; not personalised investment advice or an offer to buy or sell securities. Pivot Tech is not a broker or a SEBI-registered investment adviser. Investments carry risk; historical performance does not guarantee future returns. Figures may be delayed, incomplete or revised; verify material figures against company and exchange disclosures.";
const TERMS_URL = "https://pivotnow.in/terms";
const PRIVACY_URL = "https://pivotnow.in/privacy";

export async function loadFinancialExport(symbol: string, basis: "consolidated" | "standalone"): Promise<FinancialExport> {
  const responses = await Promise.all((Object.keys(STATEMENT_LABELS) as StatementType[]).map(type => getStatement(symbol, type, basis, 0)));
  // Never silently ship a partial workbook after a failed request.
  const statements = responses.map(response => {
    if (isError(response)) throw new Error(response.error.message || "Financial history could not be loaded.");
    return response.data;
  }).filter(statement => statement.available && statement.rows.length && statement.periods.length);
  if (!statements.length) throw new Error("No filed financial history is available for this company.");
  return {symbol, statements};
}

// Escape text formulas while preserving numeric negatives as real numbers.
export function safeText(value: string): string { return /^[\s]*[=+@-]/.test(value) ? "'" + value : value; }
function csvCell(value: string | number | null): string {
  if (value === null) return "";
  const text = typeof value === "number" ? String(value) : safeText(value);
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}
export function financialCsv(data: FinancialExport): string {
  const rows: (string | number | null)[][] = [["symbol","company","statement","basis","section","metric","statement_unit","period","value","reported_value","distributed_by","rights_notice","usage_notice","terms_url","privacy_url"]];
  for (const statement of data.statements) for (const row of statement.rows) for (const period of [...statement.periods].reverse()) {
    const value = row.values[period];
    rows.push([data.symbol, statement.company?.name ?? data.symbol, statement.statement, statement.basis,
      row.section ?? "", row.line_item, statement.unit ?? "", period,
      typeof value === "number" && Number.isFinite(value) ? value : null, row.value_texts?.[period] ?? "", PROVIDER,
      RIGHTS_NOTICE, USE_NOTICE, TERMS_URL, PRIVACY_URL]);
  }
  return "\uFEFF" + rows.map(row => row.map(csvCell).join(",")).join("\r\n") + "\r\n";
}

export async function financialWorkbook(data: FinancialExport) {
  const { Workbook } = await import("exceljs");
  const workbook = new Workbook();
  workbook.creator = PROVIDER; workbook.created = new Date();
  for (const statement of data.statements) {
    const periods = [...statement.periods].reverse();
    const sheet = workbook.addWorksheet(STATEMENT_LABELS[statement.statement], {
      views: [{state: "frozen", xSplit: 3, ySplit: 1}],
    });
    sheet.columns = [{header:"Section",width:28},{header:"Metric",width:46},{header:"Unit as reported",width:22},
      ...periods.map(period=>({header:period,width:18}))];
    sheet.getRow(1).height = 26;
    sheet.getRow(1).eachCell(cell => {
      cell.font={name:"Arial",size:11,bold:true,color:{argb:"FFFFFFFF"}};
      cell.fill={type:"pattern",pattern:"solid",fgColor:{argb:"FF202329"}};
      cell.alignment={vertical:"middle",wrapText:true};
    });
    for (const row of statement.rows) {
      const values = periods.map(period => {
        const value=row.values[period];
        if (typeof value === "number" && Number.isFinite(value)) return value;
        const reported=row.value_texts?.[period];
        return reported && !/^(?:[-—]|n\/a|na)$/i.test(reported.trim()) ? safeText(reported) : null;
      });
      const excelRow = sheet.addRow([safeText(row.section ?? ""), safeText(row.line_item), safeText(statement.unit ?? ""), ...values]);
      excelRow.font={name:"Arial",size:10,color:{argb:"FF202329"}};
      excelRow.alignment={vertical:"top",wrapText:true};
      periods.forEach((period,i) => {
        const cell=excelRow.getCell(i+4);
        cell.numFmt = row.value_texts?.[period]?.includes("%") ? '0.00"%"' : '#,##0.00;[Red](#,##0.00);0.00';
        cell.alignment={horizontal:typeof cell.value === "number" ? "right" : "left",vertical:"top"};
      });
    }
    sheet.autoFilter={from:{row:1,column:1},to:{row:sheet.rowCount,column:periods.length+3}};
  }
  const notes=workbook.addWorksheet("Read me");
  notes.columns=[{header:"Field",width:25},{header:"Details",width:105}];
  notes.addRows([["Company",data.statements[0]?.company?.name ?? data.symbol],["Symbol",data.symbol],
    ["Exported at (UTC)",workbook.created.toISOString()],
    ["Coverage","All stored periods and filed line items. Statements with no stored data are omitted."],
    ["Values","Numeric values retain the reported scale. Missing values are blank, never zero. No figures are estimated."],
    ["Units","Statement units are as reported. Per-share figures and ratios follow the metric's label and reported formatting; percentages remain percentage points."],
    ...data.statements.map(s=>[STATEMENT_LABELS[s.statement],`${s.basis}; ${s.periods.length} periods; unit: ${s.unit ?? "not provided"}`]),
    ["Prepared and distributed by",PROVIDER],
    ["Rights and permitted use",RIGHTS_NOTICE],
    ["Research and risk notice",USE_NOTICE],
    ["Simulations","Paper trades and backtests are simulations. Their assumptions, costs and outcomes can differ from live trading. Pivot does not place live broker orders."],
    ["Terms of use",TERMS_URL],["Privacy policy",PRIVACY_URL],["Contact","info@pivotnow.in"],
    ["Copyright",`© ${workbook.created.getUTCFullYear()} Pivot Tech. Rights in Pivot's original materials reserved.`]]);
  notes.eachRow((row, index) => {
    row.font={name:"Arial",size:11,color:{argb:"FF202329"},bold:index===1};
    row.alignment={wrapText:true,vertical:"top"};
    const detail=String(row.getCell(2).value ?? "");
    row.height=Math.max(24, Math.ceil(detail.length/100)*17+8);
  });
  notes.views=[{state:"frozen",ySplit:1}];
  return workbook;
}
export function downloadFinancialFile(contents: BlobPart, type: string, filename: string): void {
  const url=URL.createObjectURL(new Blob([contents],{type}));
  const link=document.createElement("a"); link.href=url; link.download=filename; document.body.appendChild(link); link.click(); link.remove();
  // The browser needs to consume the URL before it is revoked.
  window.setTimeout(()=>URL.revokeObjectURL(url),1000);
}

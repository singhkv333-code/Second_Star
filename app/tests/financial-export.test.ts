import { describe, it, expect, vi } from "vitest";
import { financialCsv, financialWorkbook, loadFinancialExport } from "@/lib/financial-export";
import { getStatement, type StatementResponse } from "@/lib/api";
vi.mock("@/lib/api",()=>({getStatement:vi.fn()}));
const statement: StatementResponse={available:true,statement:"profit_loss",basis:"standalone",company:null,unit:"Rs. in Cr.",source:"moneycontrol",periods:["Mar 2026","Mar 1998"],rows:[
  {section:"Operations",line_item:'Sales, "net"',values:{"Mar 2026":123456.78,"Mar 1998":0},value_texts:{"Mar 2026":"1,23,456.78","Mar 1998":"0"}},
  {section:null,line_item:"Margin (%)",values:{"Mar 2026":18.5,"Mar 1998":null},value_texts:{"Mar 2026":"18.5%","Mar 1998":"—"}},
  {section:null,line_item:"=HYPERLINK(\"bad\")",values:{"Mar 2026":-10,"Mar 1998":null},value_texts:{}}
]};
describe("financial exports",()=>{
  it("exports all periods with Pivot distribution, rights notices and importable numeric values",()=>{
    const csv=financialCsv({symbol:"HDFCBANK",statements:[statement]});
    expect(csv.startsWith("\uFEFFsymbol,company,statement,basis")).toBe(true);
    expect(csv).toContain('"Sales, ""net"""');
    expect(csv).toContain("Mar 1998,0,0,Pivot Tech");
    expect(csv).toContain("Mar 1998,,—,Pivot Tech");
    expect(csv).toContain("Mar 2026,-10,,Pivot Tech");
    expect(csv).not.toContain("moneycontrol");
    expect(csv).toContain("distributed_by,rights_notice,usage_notice,terms_url,privacy_url");
    expect(csv).toContain("applicable third-party rights and licences");
    expect(csv).toContain("not a broker or a SEBI-registered investment adviser");
    expect(csv).toContain("https://pivotnow.in/terms");
    expect(csv).toContain("https://pivotnow.in/privacy");
    expect(csv).toContain("'=HYPERLINK");
  });
  it("round-trips a real XLSX with numeric cells, frozen headers and chronological columns",async()=>{
    const workbook=await financialWorkbook({symbol:"HDFCBANK",statements:[statement]});
    const bytes=await workbook.xlsx.writeBuffer();
    const {Workbook}=await import("exceljs"); const copy=new Workbook(); await copy.xlsx.load(bytes);
    const sheet=copy.getWorksheet("Profit and loss")!;
    expect(sheet.getCell("D1").value).toBe("Mar 1998");
    expect(sheet.getCell("E1").value).toBe("Mar 2026");
    expect(sheet.getCell("D2").value).toBe(0);
    expect(sheet.getCell("E2").value).toBe(123456.78);
    expect(sheet.getCell("D3").value).toBeNull();
    expect(sheet.getCell("E3").numFmt).toBe('0.00"%"');
    expect(sheet.getCell("B4").value).toBe("'=HYPERLINK(\"bad\")");
    expect(sheet.views[0]).toMatchObject({state:"frozen",xSplit:3,ySplit:1});
    expect(sheet.autoFilter).toBeTruthy();
    const notes=copy.getWorksheet("Read me")!;
    const visibleText=copy.worksheets.flatMap(s=>s.getSheetValues()).flat(Infinity).join(" ");
    expect(visibleText).not.toContain("moneycontrol");
    expect(visibleText).toContain("Prepared and distributed by Pivot Tech");
    expect(visibleText).toContain("applicable third-party rights and licences");
    expect(visibleText).toContain("not a broker or a SEBI-registered investment adviser");
    expect(visibleText).toContain("Paper trades and backtests are simulations");
    expect(visibleText).toContain("https://pivotnow.in/terms");
    expect(visibleText).toContain("https://pivotnow.in/privacy");
    expect(visibleText).toContain("info@pivotnow.in");
    expect(notes.getRow(notes.rowCount-7).height).toBeGreaterThan(24);
  });
  it("requests untruncated history for all four statements and refuses partial failures",async()=>{
    vi.mocked(getStatement).mockResolvedValue({data:statement});
    await loadFinancialExport("HDFCBANK","standalone");
    expect(vi.mocked(getStatement).mock.calls.slice(-4).every(c=>c[3]===0&&c[2]==="standalone")).toBe(true);
    vi.mocked(getStatement).mockResolvedValueOnce({error:{code:"unavailable",message:"History unavailable"}});
    await expect(loadFinancialExport("HDFCBANK","consolidated")).rejects.toThrow("History unavailable");
  });
});

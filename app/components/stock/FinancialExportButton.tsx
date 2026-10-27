"use client";
import { useState } from "react";
import { Download, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { downloadFinancialFile, financialCsv, financialWorkbook, loadFinancialExport } from "@/lib/financial-export";

export function FinancialExportButton({symbol,basis="consolidated"}: {symbol:string;basis?:"consolidated"|"standalone"}) {
  const [busy,setBusy]=useState(false);
  async function exportFile(format:"csv"|"xlsx") {
    setBusy(true);
    try {
      const data=await loadFinancialExport(symbol,basis);
      const filename=`Pivot-${symbol.replace(/[^a-z0-9_-]/gi,"_")}-financials`;
      if(format==="csv") downloadFinancialFile(financialCsv(data),"text/csv;charset=utf-8",filename+".csv");
      else {
        const book=await financialWorkbook(data);
        const bytes=await book.xlsx.writeBuffer();
        downloadFinancialFile(new Uint8Array(bytes).buffer,"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",filename+".xlsx");
      }
    } catch(error) { toast.error(error instanceof Error ? error.message : "The export could not be created."); }
    finally { setBusy(false); }
  }
  return <DropdownMenu><DropdownMenuTrigger asChild>
    <Button variant="outline" size="sm" disabled={busy} aria-label="Export financial history" title="Export all available financial history">
      {busy?<Loader2 className="animate-spin" aria-hidden/>:<Download aria-hidden/>} {busy?"Exporting…":"Export"}
    </Button>
  </DropdownMenuTrigger><DropdownMenuContent align="end">
    <DropdownMenuItem onSelect={()=>void exportFile("xlsx")}>Excel (.xlsx)</DropdownMenuItem>
    <DropdownMenuItem onSelect={()=>void exportFile("csv")}>CSV (.csv)</DropdownMenuItem>
  </DropdownMenuContent></DropdownMenu>;
}

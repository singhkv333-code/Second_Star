import { StockDetailPage } from "@/components/StockDetailPage";

export default function Page({ params }: { params: { symbol: string } }) {
  return <StockDetailPage symbol={params.symbol.toUpperCase()} />;
}

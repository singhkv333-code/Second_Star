import { render, screen, fireEvent, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HomeWelcome } from "@/components/HomeWelcome";

vi.mock("@paper-design/shaders-react", () => ({ MeshGradient: () => null }));

it("opens the chart workspace and submits a trimmed question only once", async () => {
  const onOpenChart = vi.fn();
  const onSendPrompt = vi.fn();
  const user = userEvent.setup();
  render(<HomeWelcome onOpenChart={onOpenChart} onSendPrompt={onSendPrompt} />);
  await user.click(screen.getByRole("button", { name: /Explore charts/ }));
  expect(onOpenChart).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: "Send question" })).toBeDisabled();
  const input = screen.getByRole("textbox", { name: "Ask Pivot" });
  await user.type(input, "  Explain NIFTY  ");
  await user.keyboard("{Enter}");
  expect(onSendPrompt).toHaveBeenCalledWith("Explain NIFTY");
  expect(onSendPrompt).toHaveBeenCalledTimes(1);
  expect(input).toHaveValue("");
});

it("keeps multiline and IME input in the composer", async () => {
  const onSendPrompt = vi.fn();
  const user = userEvent.setup();
  render(<HomeWelcome onOpenChart={vi.fn()} onSendPrompt={onSendPrompt} />);
  const input = screen.getByRole("textbox", { name: "Ask Pivot" });
  await user.type(input, "NIFTY");
  await user.keyboard("{Shift>}{Enter}{/Shift}");
  expect(input).toHaveValue("NIFTY\n");
  fireEvent.keyDown(input, { key: "Enter", isComposing: true });
  expect(onSendPrompt).not.toHaveBeenCalled();
});

it("uses the plus menu to prepare a question without submitting it", async () => {
  const user = userEvent.setup();
  const onSendPrompt = vi.fn();
  render(<HomeWelcome onOpenChart={vi.fn()} onSendPrompt={onSendPrompt} />);
  await user.click(screen.getByRole("button", { name: "Add a chart question" }));
  await user.click(await screen.findByRole("menuitem", { name: "Explain Bitcoin’s latest price action." }));
  expect(screen.getByRole("textbox", { name: "Ask Pivot" })).toHaveValue("Explain Bitcoin’s latest price action.");
  expect(onSendPrompt).not.toHaveBeenCalled();
});

it("switches recorded chart intervals and keeps the selected range clear", async () => {
  const user = userEvent.setup();
  render(<HomeWelcome onOpenChart={vi.fn()} onSendPrompt={vi.fn()} />);
  const ranges = screen.getByRole("group", { name: "Bitcoin chart range" });
  await user.click(within(ranges).getByRole("button", { name: "1W" }));
  expect(within(ranges).getByRole("button", { name: "1W" })).toHaveAttribute("aria-pressed", "true");
  expect(within(ranges).getByRole("button", { name: "1D" })).toHaveAttribute("aria-pressed", "false");
  expect(screen.getByRole("img", { name: /Bitcoin, recorded 1W line chart/ })).toBeInTheDocument();
});
it("highlights the selected allocation and previews another holding on hover", async () => {
  const user = userEvent.setup();
  render(<HomeWelcome onOpenChart={vi.fn()} onSendPrompt={vi.fn()} />);
  await user.click(screen.getByRole("button", { name: "TCS" }));
  expect(screen.getByRole("button", { name: "TCS" })).toHaveAttribute("aria-pressed", "true");
  const tcs = screen.getByRole("button", { name: "Select TCS allocation" });
  const reliance = screen.getByRole("button", { name: "Select RELIANCE allocation" });
  expect(tcs).toHaveAttribute("stroke-width", "9");
  await user.hover(screen.getByRole("button", { name: "RELIANCE" }));
  expect(reliance).toHaveAttribute("stroke-width", "9");
  expect(tcs).toHaveAttribute("stroke-width", "7");
  await user.unhover(screen.getByRole("button", { name: "RELIANCE" }));
  expect(tcs).toHaveAttribute("stroke-width", "9");
  expect(screen.queryByText("Example portfolio")).not.toBeInTheDocument();
});

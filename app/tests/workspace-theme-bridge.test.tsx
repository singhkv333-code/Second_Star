import React from "react";
import { act, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChartFrame } from "@/components/chart/ChartFrame";
import type { WorkspacePalette } from "@/lib/workspace-theme";

const palette: WorkspacePalette = {id:"nord",name:"Nord",surface:"#2e3440",chrome:"#232933",border:"#485364",accent:"#88c0d0",up:"#a3be8c",down:"#e1838b",finish:"flat"};
afterEach(() => { delete window.PivotWorkspaceTheme; });
describe("workspace palette bridge", () => {
  it("sends the full palette at readiness and updates it without remounting the iframe", () => {
    let current = palette;
    window.PivotWorkspaceTheme = {getTheme:()=>current, setTheme:vi.fn(), presets:[palette],openPicker:vi.fn(),apply:vi.fn(),chartPalette:()=>({dark:true})};
    const {container} = render(<ChartFrame theme="dark" />);
    const frame=container.querySelector("iframe")!;
    const post=vi.spyOn(frame.contentWindow!,"postMessage");
    fireEvent.load(frame);
    expect(post).toHaveBeenCalledWith({type:"pivot:workspace-theme",theme:palette},window.location.origin);
    act(() => window.dispatchEvent(new MessageEvent("message",{origin:window.location.origin,source:frame.contentWindow,data:{type:"chart:ready"}})));
    const src=frame.src;
    post.mockClear();
    current={...palette,surface:"#172635",finish:"wash"};
    act(() => window.dispatchEvent(new CustomEvent("pivot:workspace-colors")));
    expect(post).toHaveBeenCalledWith({type:"pivot:workspace-theme",theme:current},window.location.origin);
    expect(container.querySelector("iframe")).toBe(frame);
    expect(frame.src).toBe(src);
    expect(post.mock.calls.some(([msg])=>msg.type==="pivot:theme")).toBe(false);
  });
  it("ignores readiness messages from another origin", () => {
    const {container} = render(<ChartFrame theme="light" />);
    const frame=container.querySelector("iframe")!;
    act(() => window.dispatchEvent(new MessageEvent("message",{origin:"https://other.test",source:frame.contentWindow,data:{type:"chart:ready"}})));
    expect(frame.style.visibility).toBe("hidden");
  });
});

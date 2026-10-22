import React from "react";
import { windowStartResizeDragging } from "../tauri";

export function WindowResizeHandles() {
  const onStartResize = (direction: string) => (e: React.PointerEvent | React.MouseEvent) => {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    void windowStartResizeDragging(direction);
  };

  const noDrag = { WebkitAppRegion: "no-drag" } as React.CSSProperties;

  const handleProps = (direction: string, className: string) => ({
    className: `pointer-events-auto fixed select-none ${className}`,
    style: noDrag,
    "data-direction": direction,
    onPointerDown: onStartResize(direction),
    onMouseDown: onStartResize(direction),
  });

  return (
    <div className="fixed inset-0 pointer-events-none z-[100] select-none" aria-hidden="true">
      {/* 4 Corners (12px x 12px for easy grabbing) */}
      <div {...handleProps("NorthWest", "top-0 left-0 w-3 h-3 cursor-nwse-resize")} />
      <div {...handleProps("NorthEast", "top-0 right-0 w-3 h-3 cursor-nesw-resize")} />
      <div {...handleProps("SouthWest", "bottom-0 left-0 w-3 h-3 cursor-nesw-resize")} />
      <div {...handleProps("SouthEast", "bottom-0 right-0 w-3 h-3 cursor-nwse-resize")} />

      {/* 4 Edges */}
      <div {...handleProps("North", "top-0 left-3 right-3 h-1.5 cursor-ns-resize")} />
      <div {...handleProps("South", "bottom-0 left-3 right-3 h-1.5 cursor-ns-resize")} />
      <div {...handleProps("West", "left-0 top-3 bottom-3 w-1.5 cursor-ew-resize")} />
      <div {...handleProps("East", "right-0 top-3 bottom-3 w-1.5 cursor-ew-resize")} />
    </div>
  );
}

import { createElement, useEffect, useRef } from "react";
import type { ComponentProps } from "react";
import Plotly from "plotly.js-basic-dist-min";
import createPlotlyComponent from "react-plotly.js/factory";

const PlotlyComponent = createPlotlyComponent(Plotly);

type PlotProps = ComponentProps<typeof PlotlyComponent>;

export function Plot(props: PlotProps) {
  const wrapperRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const el = wrapperRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if (!(e.ctrlKey || e.metaKey)) {
        e.stopPropagation();
      }
    };
    el.addEventListener("wheel", onWheel, { capture: true });
    return () =>
      el.removeEventListener("wheel", onWheel, { capture: true } as any);
  }, []);

  return createElement(
    "div",
    { ref: wrapperRef, style: { width: "100%", height: "100%" } },
    createElement(PlotlyComponent, props),
  );
}

export const DARK_LAYOUT: Partial<Plotly.Layout> = {
  paper_bgcolor: "transparent",
  plot_bgcolor: "transparent",
  font: { color: "#a1a1aa", size: 11 },
  xaxis: { gridcolor: "#3f3f46", linecolor: "#52525b", zeroline: false },
  yaxis: { gridcolor: "#3f3f46", linecolor: "#52525b", zeroline: false },
  hovermode: "x unified",
  dragmode: "pan",
  margin: { t: 20, r: 30, b: 50, l: 60 },
  legend: { bgcolor: "transparent", font: { color: "#a1a1aa" } },
};

export const PLOTLY_CONFIG: Partial<Plotly.Config> = {
  scrollZoom: true,
  displayModeBar: "hover" as any,
  displaylogo: false,
  responsive: true,
  modeBarButtonsToRemove: ["lasso2d", "select2d", "autoScale2d"],
};

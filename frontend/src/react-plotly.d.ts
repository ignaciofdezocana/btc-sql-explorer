declare module 'react-plotly.js' {
  import type { ComponentType } from 'react';
  interface PlotParams {
    data: object[];
    layout?: Record<string, unknown>;
    config?: object;
    style?: object;
    useResizeHandler?: boolean;
  }
  const Plot: ComponentType<PlotParams>;
  export default Plot;
}

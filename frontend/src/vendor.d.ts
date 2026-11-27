declare module "*/chessground.min.js" {
  export const Chessground: any;
}

declare module "*/chess.js" {
  export const Chess: any;
}

declare module "*/promotion.js" {
  export function needsPromotion(
    chess: any,
    orig: string,
    dest: string,
  ): boolean;
  export function askPromotion(
    color: string,
    boardEl: HTMLElement,
    destSquare: string,
    orientation?: string,
  ): Promise<string | null>;
}

declare module "*/engine.js" {
  export interface EngineLineInfo {
    pv: string[];
    eval_cp: number | null;
    eval_mate: number | null;
  }

  export class StockfishAnalysis {
    constructor(options: {
      workerUrl: string;
      fallbackWorkerUrl?: string;
      lineCount?: number;
      onLines?: (lines: EngineLineInfo[], fen: string) => void;
      onError?: (message: string) => void;
      debounceMs?: number;
    });
    analyze(fen: string): void;
    analyzeNow(fen?: string): void;
    dispose(): void;
  }
}

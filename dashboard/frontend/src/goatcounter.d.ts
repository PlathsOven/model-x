interface GoatCounterCountArgs {
  path?: string | (() => string);
  title?: string | (() => string);
  referrer?: string | (() => string);
  event?: boolean;
}

interface GoatCounter {
  count?: (args?: GoatCounterCountArgs) => void;
  no_onload?: boolean;
}

declare global {
  interface Window {
    goatcounter?: GoatCounter;
  }
}

export {};

import { useEffect, useMemo, useRef } from 'react';
import CodeMirror from '@uiw/react-codemirror';
import { sql } from '@codemirror/lang-sql';
import { oneDark } from '@codemirror/theme-one-dark';
import { Prec } from '@codemirror/state';
import { keymap } from '@codemirror/view';

const DEFAULT_SQL = `-- Write a SQL query or pick an example from the sidebar
SELECT * FROM blocks LIMIT 10;`;

type Props = {
  value: string;
  onChange: (v: string) => void;
  onExecute: () => void;
  height: number;
  onHeightChange: (h: number) => void;
  disabled?: boolean;
};

export function SqlEditor({ value, onChange, onExecute, height, onHeightChange, disabled }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const runRef = useRef(onExecute);
  runRef.current = onExecute;

  // Highest-priority keymap: Cmd/Ctrl+Enter runs query
  const runKeymap = useMemo(
    () =>
      Prec.highest(
        keymap.of([
          { key: 'Mod-Enter', run: () => { runRef.current(); return true; } },
          { key: 'Ctrl-Enter', run: () => { runRef.current(); return true; } },
        ])
      ),
    []
  );

  // DOM fallback for Cmd+Enter / Ctrl+Enter
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key !== 'Enter' || disabled) return;
      if (!e.metaKey && !e.ctrlKey) return;
      const el = containerRef.current;
      const target = document.activeElement as HTMLElement | null;
      if (!el?.contains(target) && !target?.closest?.('.cm-editor')) return;
      e.preventDefault();
      e.stopPropagation();
      runRef.current();
    };
    document.addEventListener('keydown', handleKeyDown, true);
    return () => document.removeEventListener('keydown', handleKeyDown, true);
  }, [disabled]);

  // The resize is now handled in App.tsx, but we keep the prop for compatibility
  void onHeightChange;

  return (
    <div ref={containerRef} className="editor-wrap" style={{ height: `${height}px` }}>
      <CodeMirror
        value={value}
        height={`${height}px`}
        extensions={[runKeymap, sql()]}
        theme={oneDark}
        onChange={onChange}
        editable={!disabled}
        basicSetup={{
          lineNumbers: true,
          highlightActiveLineGutter: true,
          bracketMatching: true,
          closeBrackets: true,
          indentOnInput: true,
          tabSize: 2,
        }}
      />
    </div>
  );
}

export const initialSql = DEFAULT_SQL;

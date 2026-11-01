'use client';
// Code editor for lab exercises (Req 14.1, 14.8, 15.2, 15.9, 15.10, 16.4).
//
// Monaco is self-hosted: `scripts/copy-monaco.mjs` copies `min/vs` to
// `public/monaco/vs` and the loader is pointed there, so no CDN request is
// made. Monaco is imported client-side only, after mount. One editor is
// rendered per learner file, each in a labelled region.
//
// Keyboard access: every editor is reachable by Tab and is NOT a focus trap.
// The documented exit chord (Ctrl+M, Ctrl+Shift+M on macOS) is the same as in
// the terminal pane: it moves focus out of the editor to "Check my work" via
// `onExit`. Monaco's own Tab focus mode (also Ctrl+M) is therefore never left
// on by accident, because the chord is consumed before Monaco sees it.
import { useEffect, useRef, useState } from 'react';
import { useTranslations } from 'next-intl';
import type { editor as MonacoEditor } from 'monaco-editor';
import { isExitChord, isMacPlatform, languageForFile } from '../editor-chord.js';

/** Where the self-hosted Monaco assets are served from (see copy-monaco.mjs). */
export const MONACO_BASE_PATH = '/monaco/vs';

export interface EditorFile {
  readonly name: string;
  readonly content: string;
}

export interface CodeEditorProps {
  /** Learner files; one editor is rendered per file, keyed by name. */
  readonly files: readonly EditorFile[];
  /** Called with the file name and new content on every edit. */
  readonly onChange: (name: string, content: string) => void;
  /** Called when the exit chord is pressed; the caller focuses "Check my work". */
  readonly onExit: () => void;
  readonly readOnly?: boolean;
}

export function CodeEditor({ files, onChange, onExit, readOnly = false }: CodeEditorProps) {
  const t = useTranslations('Labs');
  const [mac, setMac] = useState(false);
  useEffect(() => {
    setMac(isMacPlatform(navigator.platform));
  }, []);
  const shortcut = mac ? t('shortcut.mac') : t('shortcut.default');

  return (
    <section aria-label={t('editorLabel')} data-testid="code-editor">
      <p id="code-editor-hint">{t('shortcut.leaveEditor', { shortcut })}</p>
      {files.map((file) => (
        <FileEditor
          key={file.name}
          file={file}
          mac={mac}
          readOnly={readOnly}
          onChange={onChange}
          onExit={onExit}
        />
      ))}
    </section>
  );
}

interface FileEditorProps {
  readonly file: EditorFile;
  readonly mac: boolean;
  readonly readOnly: boolean;
  readonly onChange: (name: string, content: string) => void;
  readonly onExit: () => void;
}

function FileEditor({ file, mac, readOnly, onChange, onExit }: FileEditorProps) {
  const t = useTranslations('Labs');
  const host = useRef<HTMLDivElement>(null);
  const instance = useRef<MonacoEditor.IStandaloneCodeEditor | null>(null);
  // Latest callbacks/flags without recreating the editor.
  const live = useRef({ onChange, onExit, mac, readOnly, name: file.name });
  live.current = { onChange, onExit, mac, readOnly, name: file.name };
  const initial = useRef(file.content);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let disposed = false;
    const disposables: { dispose(): void }[] = [];

    void (async () => {
      const { default: loader } = await import('@monaco-editor/loader');
      loader.config({ paths: { vs: MONACO_BASE_PATH } });
      const monaco: typeof import('monaco-editor') = await loader.init();
      if (disposed || !host.current) return;

      const editor = monaco.editor.create(host.current, {
        value: initial.current,
        language: languageForFile(live.current.name),
        ariaLabel: live.current.name,
        automaticLayout: true,
        minimap: { enabled: false },
        scrollBeyondLastLine: false,
        accessibilitySupport: 'auto',
        readOnly: live.current.readOnly,
      });
      instance.current = editor;
      setLoading(false);

      disposables.push(
        editor.onDidChangeModelContent(() => {
          live.current.onChange(live.current.name, editor.getValue());
        }),
        // Consume the exit chord before Monaco toggles its own Tab focus mode,
        // then hand focus to the caller. Other keys are untouched.
        editor.onKeyDown((e) => {
          if (isExitChord(e.browserEvent, live.current.mac)) {
            e.preventDefault();
            e.stopPropagation();
            live.current.onExit();
          }
        }),
      );
      disposables.push(editor);
    })();

    return () => {
      disposed = true;
      for (const d of disposables) d.dispose();
      instance.current = null;
    };
  }, []);

  useEffect(() => {
    instance.current?.updateOptions({ readOnly });
  }, [readOnly]);

  // Apply external content changes (e.g. a reset) without echoing our own edits.
  useEffect(() => {
    const ed = instance.current;
    if (ed && ed.getValue() !== file.content) ed.setValue(file.content);
  }, [file.content]);

  return (
    <div role="group" aria-label={file.name} data-testid={`code-editor-file-${file.name}`}>
      <h3>{file.name}</h3>
      {loading ? <p role="status">{t('status.loading')}</p> : null}
      <div
        ref={host}
        aria-describedby="code-editor-hint"
        style={{ height: '20rem', width: '100%' }}
      />
    </div>
  );
}

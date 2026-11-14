import { CSSProperties, useEffect, useRef } from "react";
import { useI18n } from "../i18n";

/*
 * Rich text for figure labels. Stored markup (understood by the server renderer, see figures/richtext.py):
 * <b>, <i>, <u>, <sup>, <sub>, <span color="#rrggbb">. Anything else is literal text.
 */

const TAG_RE = /<(\/?)(b|i|u|sup|sub)>|<span color="(#[0-9a-fA-F]{3,8})">|<\/span>/g;

const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
function unesc(s: string) {
  const t = document.createElement("textarea");
  t.innerHTML = s;
  return t.value;
}

export function isRich(s: string | null | undefined) {
  TAG_RE.lastIndex = 0;
  return !!s && TAG_RE.test(s);
}

export function plainText(s: string | null | undefined) {
  return unesc((s ?? "").replace(TAG_RE, ""));
}

/** markup -> editor HTML (text chunks are escaped, only known tags pass) */
export function markupToHtml(s: string): string {
  let out = "";
  let pos = 0;
  TAG_RE.lastIndex = 0;
  for (let m = TAG_RE.exec(s); m; m = TAG_RE.exec(s)) {
    out += esc(unesc(s.slice(pos, m.index)));
    out += m[3] ? `<span style="color:${m[3]}">` : m[0];
    pos = m.index + m[0].length;
  }
  return out + esc(unesc(s.slice(pos)));
}

function toHex(c: string): string | null {
  if (!c) return null;
  if (c.startsWith("#")) return c.toLowerCase();
  const m = c.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
  if (!m) return null;
  const hex = "#" + [m[1], m[2], m[3]].map((v) => Number(v).toString(16).padStart(2, "0")).join("");
  return hex === "#000000" ? null : hex;
}

/** editor DOM -> markup */
export function htmlToMarkup(root: Node): string {
  const walk = (n: Node): string => {
    if (n.nodeType === Node.TEXT_NODE) return esc((n.textContent ?? "").replace(/ /g, " "));
    if (n.nodeType !== Node.ELEMENT_NODE) return "";
    const el = n as HTMLElement;
    const tag = el.tagName.toLowerCase();
    if (tag === "br") return " ";
    let inner = Array.from(el.childNodes).map(walk).join("");
    if (!inner) return "";
    const st = el.style;
    const wraps: [string, string][] = [];
    if (tag === "b" || tag === "strong" || st.fontWeight === "bold" || Number(st.fontWeight) >= 600) wraps.push(["<b>", "</b>"]);
    if (tag === "i" || tag === "em" || st.fontStyle === "italic") wraps.push(["<i>", "</i>"]);
    if (tag === "u" || /underline/.test(st.textDecoration || st.textDecorationLine || "")) wraps.push(["<u>", "</u>"]);
    if (tag === "sup" || st.verticalAlign === "super") wraps.push(["<sup>", "</sup>"]);
    if (tag === "sub" || st.verticalAlign === "sub") wraps.push(["<sub>", "</sub>"]);
    const color = toHex(el.getAttribute("color") || st.color);
    if (color) wraps.push([`<span color="${color}">`, "</span>"]);
    for (const [o, c] of wraps.reverse()) inner = o + inner + c;
    return (tag === "div" || tag === "p") && el.previousSibling ? " " + inner : inner;
  };
  return Array.from(root.childNodes).map(walk).join("").replace(/\s+/g, " ").replace(/^ | $/g, "");
}

interface Props {
  value: string;
  onChange?: (v: string) => void;
  onCommit?: (v: string) => void; // Enter / leaving the field (inline editing)
  onCancel?: () => void;
  disabled?: boolean;
  autoFocus?: boolean;
  className?: string;
  style?: CSSProperties;
  ariaLabel?: string;
}

/** One-line rich text field with a small formatting toolbar (B, I, U, x², x₂, colour, clear). */
export default function RichInput({ value, onChange, onCommit, onCancel, disabled, autoFocus, className, style, ariaLabel }: Props) {
  const { lang } = useI18n();
  const ed = useRef<HTMLDivElement>(null);
  const last = useRef<string | null>(null);
  const range = useRef<Range | null>(null);
  const T = (fr: string, en: string) => (lang === "fr" ? fr : en);

  useEffect(() => {
    if (!ed.current || value === last.current) return;
    ed.current.innerHTML = markupToHtml(value ?? "");
    last.current = value;
  }, [value]);

  useEffect(() => {
    if (!autoFocus || !ed.current) return;
    ed.current.focus();
    const r = document.createRange();
    r.selectNodeContents(ed.current);
    const sel = window.getSelection();
    sel?.removeAllRanges();
    sel?.addRange(r);
  }, [autoFocus]);

  const current = () => (ed.current ? htmlToMarkup(ed.current) : value);
  const emit = () => {
    const v = current();
    last.current = v;
    onChange?.(v);
  };
  const saveRange = () => {
    const sel = window.getSelection();
    if (sel && sel.rangeCount && ed.current?.contains(sel.anchorNode)) range.current = sel.getRangeAt(0).cloneRange();
  };
  const restore = () => {
    ed.current?.focus();
    if (range.current) {
      const sel = window.getSelection();
      sel?.removeAllRanges();
      sel?.addRange(range.current);
    }
  };
  const cmd = (c: string, arg?: string) => {
    restore();
    document.execCommand("styleWithCSS", false, "false");
    document.execCommand(c, false, arg);
    saveRange();
    emit();
  };
  const btn = (label: React.ReactNode, title: string, c: string, cls = "") => (
    <button type="button" className={`rt-btn ${cls}`} title={title} aria-label={title} disabled={disabled}
      onMouseDown={(e) => { e.preventDefault(); cmd(c); }}>{label}</button>
  );

  return (
    <div className={`rich-input ${className ?? ""} ${disabled ? "disabled" : ""}`} style={style}
      onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) onCommit?.(current()); }}>
      <div className="rt-toolbar" role="toolbar" aria-label={T("Mise en forme", "Formatting")}>
        {btn(<b>B</b>, T("Gras", "Bold"), "bold")}
        {btn(<i>I</i>, T("Italique", "Italic"), "italic")}
        {btn(<u>U</u>, T("Souligné", "Underline"), "underline")}
        {btn(<>x<sup>2</sup></>, T("Exposant", "Superscript"), "superscript")}
        {btn(<>x<sub>2</sub></>, T("Indice", "Subscript"), "subscript")}
        <label className="rt-btn rt-color" title={T("Couleur", "Colour")} onMouseDown={saveRange}>
          <span>A</span>
          <input type="color" disabled={disabled} aria-label={T("Couleur", "Colour")} onChange={(e) => cmd("foreColor", e.target.value)} />
        </label>
        {btn("⌫", T("Effacer la mise en forme", "Clear formatting"), "removeFormat", "rt-clear")}
      </div>
      <div ref={ed} className="rt-editor" contentEditable={!disabled} suppressContentEditableWarning role="textbox" aria-label={ariaLabel}
        aria-multiline="false" spellCheck={false}
        onInput={emit} onKeyUp={saveRange} onMouseUp={saveRange}
        onKeyDown={(e) => {
          if (e.key === "Enter") { e.preventDefault(); onCommit?.(current()); }
          if (e.key === "Escape") { e.preventDefault(); onCancel?.(); }
          if ((e.metaKey || e.ctrlKey) && ["b", "i", "u"].includes(e.key.toLowerCase())) {
            e.preventDefault();
            cmd({ b: "bold", i: "italic", u: "underline" }[e.key.toLowerCase()]!);
          }
        }}
        onPaste={(e) => { e.preventDefault(); document.execCommand("insertText", false, e.clipboardData.getData("text/plain").replace(/\s+/g, " ")); }}
      />
    </div>
  );
}

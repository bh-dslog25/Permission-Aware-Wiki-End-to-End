import { useEffect, useRef, useState } from "react";
import type { Persona } from "../api";
import { CheckIcon, ChevronDownIcon, ShieldIcon } from "./Icons";

type PersonaSwitcherProps = {
  personas: Persona[];
  activePersona: Persona;
  activeContextId: string;
  onSelectPersona: (persona: Persona) => void;
  onSelectContext: (contextId: string) => void;
};

export default function PersonaSwitcher({
  personas,
  activePersona,
  activeContextId,
  onSelectPersona,
  onSelectContext,
}: PersonaSwitcherProps) {
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function closeOnOutsideClick(event: MouseEvent) {
      if (!containerRef.current?.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }

    document.addEventListener("mousedown", closeOnOutsideClick);
    return () => document.removeEventListener("mousedown", closeOnOutsideClick);
  }, []);

  const currentCtx =
    activePersona.contexts.find((c) => c.id === activeContextId) ??
    activePersona.contexts[0];

  return (
    <div className="relative" ref={containerRef}>
      <button
        aria-expanded={isOpen}
        aria-haspopup="listbox"
        className="flex w-full items-center gap-3 rounded-xl border border-[#dde2dd] bg-white px-3 py-2.5 text-left transition hover:border-[#bec9c0] hover:bg-[#fafbf9]"
        onClick={() => setIsOpen((open) => !open)}
        type="button"
      >
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-[#e7f1ea] text-[11px] font-bold text-[#236743]">
          {activePersona.initials}
        </div>
        <div className="min-w-0 flex-1">
          <p className="truncate text-xs font-bold text-[#2d352f]">{activePersona.name}</p>
          <p className="mt-0.5 truncate text-[11px] text-[#7d857e]">
            {currentCtx?.label ?? activePersona.title}
          </p>
        </div>
        <ChevronDownIcon
          className={`h-4 w-4 shrink-0 text-[#7c847e] transition ${isOpen ? "rotate-180" : ""}`}
        />
      </button>

      {isOpen && (
        <div
          className="absolute bottom-[calc(100%+0.5rem)] left-0 z-50 w-[21rem] max-h-[30rem] overflow-y-auto rounded-2xl border border-[#dfe4df] bg-white p-2 shadow-[0_18px_60px_rgba(28,45,34,0.16)]"
          role="listbox"
        >
          {/* Nếu persona hiện tại có nhiều ngữ cảnh (kiêm nhiệm), cho phép đổi ngữ cảnh ngay */}
          {activePersona.contexts.length > 1 && (
            <div className="mb-2 border-b border-[#e6e9e6] pb-2">
              <div className="flex items-center gap-2 px-3 pb-1.5 pt-1">
                <ShieldIcon className="h-3.5 w-3.5 text-[#28734d]" />
                <p className="text-[10px] font-bold uppercase tracking-[0.12em] text-[#69726b]">
                  Ngữ cảnh hiện tại ({activePersona.name})
                </p>
              </div>
              {activePersona.contexts.map((ctx) => {
                const isSelected = ctx.id === activeContextId;
                return (
                  <button
                    key={ctx.id}
                    className={`flex w-full items-center justify-between rounded-xl px-3 py-2 text-left text-xs transition ${
                      isSelected ? "bg-[#edf5ef] font-bold text-[#257047]" : "hover:bg-[#f5f7f4] text-[#424a44]"
                    }`}
                    onClick={() => {
                      onSelectContext(ctx.id);
                      setIsOpen(false);
                    }}
                    type="button"
                  >
                    <span>{ctx.label}</span>
                    {isSelected && <CheckIcon className="h-3.5 w-3.5 text-[#257047]" />}
                  </button>
                );
              })}
            </div>
          )}

          <div className="flex items-center gap-2 px-3 pb-2 pt-1.5">
            <ShieldIcon className="h-4 w-4 text-[#28734d]" />
            <p className="text-[11px] font-bold uppercase tracking-[0.12em] text-[#69726b]">
              Đổi tài khoản
            </p>
          </div>

          <div className="space-y-1">
            {personas.map((persona) => {
              const active = persona.id === activePersona.id;
              return (
                <button
                  aria-selected={active}
                  className={`flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left transition ${
                    active ? "bg-[#edf5ef]" : "hover:bg-[#f5f7f4]"
                  }`}
                  key={persona.id}
                  onClick={() => {
                    onSelectPersona(persona);
                    setIsOpen(false);
                  }}
                  role="option"
                  type="button"
                >
                  <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-[#e7f1ea] text-[10px] font-bold text-[#236743]">
                    {persona.initials}
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-xs font-bold text-[#29322c]">{persona.name}</p>
                    <p className="truncate text-[11px] text-[#768078]">{persona.title || persona.description}</p>
                  </div>
                  {active && <CheckIcon className="ml-auto h-4 w-4 text-[#257047]" />}
                </button>
              );
            })}
          </div>

          <p className="mx-2 mt-2 border-t border-[#e6e9e6] px-1 py-2 text-[11px] leading-4 text-[#888f89]">
            Quyền được phân định nghiêm ngặt theo từng đơn vị và membership trong cơ sở dữ liệu.
          </p>
        </div>
      )}
    </div>
  );
}

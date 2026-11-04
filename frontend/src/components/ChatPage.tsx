import { FormEvent, useEffect, useRef, useState } from "react";
import {
  askQuestion,
  fetchConversation,
  fetchConversations,
  fetchPersonas,
  submitFeedback,
  type ChatAnswer,
  type ConversationSummary,
  type Persona,
  type User,
  toConversationEntry,
} from "../api";
import ContextBadge from "./ContextBadge";
import {
  AlertIcon,
  BookIcon,
  LogOutIcon,
  MessageIcon,
  PlusIcon,
  SendIcon,
  ShieldIcon,
  SparklesIcon,
} from "./Icons";
import PersonaSwitcher from "./PersonaSwitcher";
import SourceCard from "./SourceCard";

type ChatPageProps = {
  user: User;
  onLogout: () => void;
  onOpenOwnerPortal?: () => void;
  onOpenAdminPortal?: () => void;
};

type ConversationEntry = {
  id: string;
  question: string;
  answer: ChatAnswer;
};

const suggestions = [
  "Quy định nghỉ phép của tôi?",
  "Quy định nghỉ phép năm 2024?",
  "Tôi có được xem lương Ban điều hành?",
  "Chính sách thưởng Tết năm nay?",
];

export default function ChatPage({ user, onLogout, onOpenOwnerPortal, onOpenAdminPortal }: ChatPageProps) {
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [activePersona, setActivePersona] = useState<Persona>({
    id: user.id,
    name: user.name,
    email: user.email,
    title: user.title,
    role: user.role,
    clearance: user.clearance,
    description: user.description,
    contexts: user.contexts,
    defaultContextId: user.defaultContextId,
    initials: user.initials,
  });
  const [activeContextId, setActiveContextId] = useState<string>(user.defaultContextId);
  const [question, setQuestion] = useState("");
  const [conversation, setConversation] = useState<ConversationEntry[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [recentConversations, setRecentConversations] = useState<ConversationSummary[]>([]);
  const [historyError, setHistoryError] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    void fetchPersonas().then((list) => {
      setPersonas(list);
      const matched = list.find((p) => p.id === user.id);
      if (matched) {
        setActivePersona(matched);
        setActiveContextId(matched.defaultContextId);
        setConversation([]);
        setConversationId(null);
      }
    });
  }, [user]);

  useEffect(() => {
    let isCurrent = true;
    setIsLoadingHistory(true);
    setRecentConversations([]);
    setHistoryError("");
    void fetchConversations(activePersona.id)
      .then((items) => {
        if (isCurrent) setRecentConversations(items);
      })
      .catch((error: unknown) => {
        if (isCurrent) {
          setHistoryError(error instanceof Error ? error.message : "Không tải được lịch sử.");
        }
      })
      .finally(() => {
        if (isCurrent) setIsLoadingHistory(false);
      });
    return () => {
      isCurrent = false;
    };
  }, [activePersona.id]);

  async function submitQuestion(nextQuestion: string) {
    const cleanQuestion = nextQuestion.trim();
    if (!cleanQuestion || isLoading) return;

    setQuestion("");
    setIsLoading(true);
    try {
      const answer = await askQuestion(
        cleanQuestion,
        activePersona.id,
        activeContextId,
        conversationId ?? undefined,
      );
      if (answer.conversationId) setConversationId(answer.conversationId);
      setConversation((items) => [
        ...items,
        { id: answer.id, question: cleanQuestion, answer },
      ]);
      try {
        setRecentConversations(await fetchConversations(activePersona.id));
        setHistoryError("");
      } catch (historyLoadError) {
        setHistoryError(
          historyLoadError instanceof Error
            ? historyLoadError.message
            : "Không tải được lịch sử hội thoại.",
        );
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : "Không thể gọi backend.";
      setConversation((items) => [
        ...items,
        {
          id: crypto.randomUUID(),
          question: cleanQuestion,
          answer: {
            id: crypto.randomUUID(),
            status: "denied",
            content: message,
            details: ["Kiểm tra backend FastAPI, PostgreSQL, OpenFGA và dữ liệu ingest."],
            sources: [],
            answeredAt: new Intl.DateTimeFormat("vi-VN", {
              hour: "2-digit",
              minute: "2-digit",
            }).format(new Date()),
          },
        },
      ]);
    } finally {
      setIsLoading(false);
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void submitQuestion(question);
  }

  function startNewConversation() {
    setConversation([]);
    setConversationId(null);
    setQuestion("");
    inputRef.current?.focus();
  }

  async function openConversation(item: ConversationSummary) {
    try {
      const detail = await fetchConversation(activePersona.id, item.id);
      setConversationId(detail.id);
      setActiveContextId(detail.context_id);
      setConversation(detail.messages.map(toConversationEntry));
      setQuestion("");
    } catch (error) {
      setHistoryError(error instanceof Error ? error.message : "Không tải được cuộc trò chuyện.");
    }
  }

  function handlePersonaChange(persona: Persona) {
    setActivePersona(persona);
    setActiveContextId(persona.defaultContextId);
    setConversation([]);
    setConversationId(null);
  }

  function handleContextChange(contextId: string) {
    setActiveContextId(contextId);
    setConversation([]);
    setConversationId(null);
  }

  return (
    <div className="flex h-screen overflow-hidden bg-[#f6f7f5] text-[#202721]">
      <aside className="hidden w-[17.5rem] shrink-0 flex-col border-r border-[#dde2dd] bg-[#f0f3ef] p-4 md:flex">
        <div className="flex items-center gap-3 px-2 py-2">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-[#165c3a] text-white">
            <SparklesIcon className="h-[18px] w-[18px]" />
          </div>
          <div>
            <p className="text-sm font-bold tracking-[-0.02em]">Appota Knowledge</p>
            <p className="text-[10px] font-bold uppercase tracking-[0.13em] text-[#899089]">
              Permission aware
            </p>
          </div>
        </div>

        <button
          className="mt-5 flex h-11 items-center justify-center gap-2 rounded-xl bg-[#165c3a] text-sm font-bold text-white shadow-sm transition hover:bg-[#104e31]"
          onClick={startNewConversation}
          type="button"
        >
          <PlusIcon className="h-4 w-4" />
          Cuộc trò chuyện mới
        </button>

        <div className="mt-7">
          <p className="px-2 text-[10px] font-bold uppercase tracking-[0.16em] text-[#8b928c]">
            Gần đây
          </p>
          <div className="mt-2 max-h-[40vh] space-y-1 overflow-y-auto">
            {isLoadingHistory && (
              <p className="px-2 py-2 text-xs text-[#899089]">Đang tải lịch sử...</p>
            )}
            {historyError && (
              <p className="px-2 py-2 text-xs text-[#a33c2f]">{historyError}</p>
            )}
            {!isLoadingHistory && !historyError && recentConversations.length === 0 && (
              <p className="px-2 py-2 text-xs text-[#899089]">Chưa có cuộc trò chuyện nào.</p>
            )}
            {recentConversations.map((item) => (
              <button
                aria-current={conversationId === item.id ? "page" : undefined}
                className={`flex w-full items-center gap-2.5 rounded-xl px-3 py-3 text-left text-xs font-semibold transition ${
                  conversationId === item.id
                    ? "bg-white text-[#27593b] shadow-sm"
                    : "text-[#4e5851] hover:bg-white/75"
                }`}
                key={item.id}
                onClick={() => void openConversation(item)}
                title={item.title}
                type="button"
              >
                <MessageIcon className="h-4 w-4 shrink-0 text-[#4d745c]" />
                <span className="truncate">{item.title}</span>
              </button>
            ))}
          </div>
        </div>

        <div className="mt-auto">
          <div className="mb-3 rounded-xl border border-[#dbe2dc] bg-[#f8faf8] p-3">
            <div className="flex items-center gap-2 text-xs font-bold text-[#356048]">
              <ShieldIcon className="h-4 w-4" />
              Truy xuất an toàn
            </div>
            <p className="mt-2 text-[11px] leading-4 text-[#7a837c]">
              Chỉ tài liệu trong phạm vi quyền của bạn mới được dùng để trả lời.
            </p>
          </div>
          <PersonaSwitcher
            activeContextId={activeContextId}
            activePersona={activePersona}
            onSelectContext={handleContextChange}
            onSelectPersona={handlePersonaChange}
            personas={personas}
          />
        </div>
      </aside>

      <main className="flex min-w-0 flex-1 flex-col bg-white">
        <header className="flex h-[4.5rem] shrink-0 items-center justify-between border-b border-[#e4e7e4] px-4 sm:px-7">
          <div className="flex items-center gap-3 md:hidden">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-[#165c3a] text-white">
              <SparklesIcon className="h-[18px] w-[18px]" />
            </div>
            <p className="text-sm font-bold">Appota Knowledge</p>
          </div>
          <div className="hidden md:block">
            <p className="text-[15px] font-bold tracking-[-0.015em]">Trợ lý tri thức nội bộ</p>
            <div className="mt-1 flex items-center gap-1.5 text-[11px] text-[#7c847e]">
              <span className="h-1.5 w-1.5 rounded-full bg-[#3c9b61]" />
              Hệ thống đang hoạt động
            </div>
          </div>

          <div className="flex items-center gap-2 sm:gap-3">
            {onOpenAdminPortal && user.role === "security" && (
              <button
                onClick={onOpenAdminPortal}
                className="flex items-center gap-1.5 rounded-xl border border-[#cbe2d3] bg-[#e8f3ed] px-3 py-1.5 text-xs font-bold text-[#165c3a] transition hover:bg-[#d8ecdf]"
                type="button"
              >
                <ShieldIcon className="h-3.5 w-3.5" />
                Quản trị
              </button>
            )}
            {onOpenOwnerPortal && (activePersona.role === "hr_owner" || activePersona.role === "security") && (
              <button
                onClick={onOpenOwnerPortal}
                className="flex items-center gap-1.5 rounded-xl bg-[#e8f3ed] border border-[#cbe2d3] px-3 py-1.5 text-xs font-bold text-[#165c3a] transition hover:bg-[#d8ecdf]"
                title="Vào trang phê duyệt dành cho Knowledge Owner"
              >
                <ShieldIcon className="h-3.5 w-3.5" />
                Duyệt tài liệu (Owner)
              </button>
            )}
            <div className="hidden text-right sm:block">
              <p className="text-xs font-bold">{user.name}</p>
              <p className="mt-0.5 text-[10px] text-[#888f89]">{user.title}</p>
            </div>
            <div className="flex h-9 w-9 items-center justify-center rounded-full bg-[#e9efe9] text-[11px] font-bold text-[#356046]">
              {user.initials}
            </div>
            <button
              aria-label="Đăng xuất"
              className="flex h-9 w-9 items-center justify-center rounded-lg text-[#737b75] transition hover:bg-[#f1f3f0] hover:text-[#253029]"
              onClick={onLogout}
              title="Đăng xuất"
              type="button"
            >
              <LogOutIcon className="h-[18px] w-[18px]" />
            </button>
          </div>
        </header>

        <div className="flex-1 overflow-y-auto">
          <div className="mx-auto flex min-h-full max-w-4xl flex-col px-4 sm:px-8">
            {conversation.length === 0 ? (
              <section className="flex flex-1 flex-col justify-center py-10">
                <div className="mx-auto w-full max-w-2xl">
                  <div className="mb-8 flex h-12 w-12 items-center justify-center rounded-2xl border border-[#dce7df] bg-[#edf5ef] text-[#28734d]">
                    <SparklesIcon className="h-6 w-6" />
                  </div>
                  <p className="mb-3 text-xs font-bold uppercase tracking-[0.16em] text-[#34704d]">
                    Xin chào, {user.name.split(" ").at(-1)}
                  </p>
                  <h1 className="max-w-xl text-3xl font-semibold leading-tight tracking-[-0.04em] sm:text-[2.6rem]">
                    Bạn muốn tìm hiểu điều gì trong hôm nay?
                  </h1>
                  <p className="mt-4 max-w-xl text-[15px] leading-7 text-[#747c76]">
                    Tôi sẽ tìm trong nguồn tri thức được phép và chỉ trả lời khi có đủ bằng chứng.
                  </p>

                  <div className="mt-8">
                    <ContextBadge activeContextId={activeContextId} persona={activePersona} />
                  </div>

                  <div className="mt-7 grid gap-2 sm:grid-cols-2">
                    {suggestions.map((suggestion) => (
                      <button
                        className="group flex min-h-[4rem] items-center justify-between rounded-xl border border-[#e1e5e1] bg-white px-4 py-3 text-left text-[13px] font-semibold leading-5 text-[#4f5952] transition hover:border-[#b9c9bc] hover:bg-[#f8faf8] hover:text-[#27593b]"
                        key={suggestion}
                        onClick={() => void submitQuestion(suggestion)}
                        type="button"
                      >
                        {suggestion}
                        <span className="ml-3 text-lg font-normal text-[#a3aaa4] transition group-hover:translate-x-0.5 group-hover:text-[#47745a]">
                          →
                        </span>
                      </button>
                    ))}
                  </div>
                </div>
              </section>
            ) : (
              <section className="space-y-9 py-8 sm:py-10">
                <ContextBadge activeContextId={activeContextId} persona={activePersona} />
                {conversation.map((entry) => (
                  <div className="space-y-6" key={entry.id}>
                    <div className="flex justify-end">
                      <div className="max-w-[85%] rounded-2xl rounded-tr-md bg-[#eef1ed] px-4 py-3 text-sm leading-6 text-[#313a33]">
                        {entry.question}
                      </div>
                    </div>
                    <AnswerBlock
                      answer={entry.answer}
                      activeContextId={activeContextId}
                      personaId={activePersona.id}
                      onSelectContext={(ctxId) => {
                        handleContextChange(ctxId);
                        void submitQuestion(entry.question);
                      }}
                    />
                  </div>
                ))}
                {isLoading && <LoadingAnswer />}
              </section>
            )}
          </div>
        </div>

        <div className="shrink-0 border-t border-[#eef0ed] bg-white px-4 pb-4 pt-3 sm:px-8 sm:pb-6">
          <form className="mx-auto max-w-4xl" onSubmit={handleSubmit}>
            <div className="rounded-2xl border border-[#d8ded9] bg-white p-2 shadow-[0_8px_30px_rgba(31,50,37,0.08)] transition focus-within:border-[#8baa94] focus-within:ring-4 focus-within:ring-[#28734d]/8">
              <textarea
                className="block max-h-32 min-h-[3.25rem] w-full resize-none bg-transparent px-3 py-2 text-sm leading-6 outline-none placeholder:text-[#999f9a]"
                onChange={(event) => setQuestion(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    void submitQuestion(question);
                  }
                }}
                placeholder="Hỏi về quy định, quy trình hoặc hướng dẫn nội bộ..."
                ref={inputRef}
                rows={1}
                value={question}
              />
              <div className="flex items-center justify-between pl-3">
                <p className="hidden text-[10px] text-[#929993] sm:block">
                  Enter để gửi · Shift + Enter để xuống dòng
                </p>
                <button
                  aria-label="Gửi câu hỏi"
                  className="ml-auto flex h-9 w-9 items-center justify-center rounded-xl bg-[#165c3a] text-white transition hover:bg-[#104e31] disabled:bg-[#cbd3cc]"
                  disabled={!question.trim() || isLoading}
                  type="submit"
                >
                  <SendIcon className="h-4 w-4" />
                </button>
              </div>
            </div>
            <p className="mt-2 text-center text-[10px] text-[#9ba09c]">
              AI có thể mắc lỗi. Hãy kiểm tra nguồn trích dẫn trước khi ra quyết định.
            </p>
          </form>
        </div>
      </main>
    </div>
  );
}

function LoadingAnswer() {
  return (
    <div className="flex items-start gap-3">
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-[#165c3a] text-white">
        <SparklesIcon className="h-4 w-4 animate-pulse" />
      </div>
      <div className="pt-2">
        <div className="flex gap-1.5">
          <span className="h-2 w-2 animate-bounce rounded-full bg-[#91a297] [animation-delay:-0.3s]" />
          <span className="h-2 w-2 animate-bounce rounded-full bg-[#91a297] [animation-delay:-0.15s]" />
          <span className="h-2 w-2 animate-bounce rounded-full bg-[#91a297]" />
        </div>
        <p className="mt-3 text-xs text-[#838b85]">
          Đang kiểm tra quyền và tìm nguồn phù hợp...
        </p>
      </div>
    </div>
  );
}

function AnswerBlock({
  answer,
  activeContextId,
  personaId,
  onSelectContext,
}: {
  answer: ChatAnswer;
  activeContextId: string;
  personaId: string;
  onSelectContext?: (contextId: string) => void;
}) {
  const isDenied = answer.status === "denied";
  const isInsufficient = answer.status === "insufficient";
  const isNeedsAttention = answer.status === "needs_attention";
  const isClarification = answer.status === "needs_clarification";
  const isEscalation = answer.status === "answer_with_escalation";
  const [feedbackSent, setFeedbackSent] = useState(false);
  const [feedbackLoading, setFeedbackLoading] = useState(false);

  async function handleAskOwner() {
    try {
      setFeedbackLoading(true);
      await submitFeedback(
        answer.id,
        activeContextId,
        "Người dùng yêu cầu giải đáp từ người phụ trách cho câu trả lời này.",
        answer.escalateTo || "Knowledge Owner",
        personaId,
      );
      setFeedbackSent(true);
    } catch (e) {
      alert("Không gửi được phản hồi: " + (e as Error).message);
    } finally {
      setFeedbackLoading(false);
    }
  }

  if (isClarification) {
    return (
      <div className="flex items-start gap-3">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-[#eef3fe] text-[#2c5282]">
          <AlertIcon className="h-4 w-4" />
        </div>
        <div className="max-w-2xl rounded-2xl border border-[#cfe2ff] bg-[#f8fbff] p-5">
          <p className="text-[11px] font-bold uppercase tracking-[0.13em] text-[#2c5282]">
            Cần xác định ngữ cảnh
          </p>
          <h2 className="mt-2 text-base font-semibold leading-7 tracking-[-0.02em] text-[#1a365d]">
            {answer.content}
          </h2>
          {answer.availableContexts && answer.availableContexts.length > 0 && (
            <div className="mt-4 flex flex-wrap gap-2">
              {answer.availableContexts.map((ctx) => (
                <button
                  key={ctx.id}
                  type="button"
                  onClick={() => onSelectContext?.(ctx.id)}
                  className="rounded-xl border border-[#b6d4fe] bg-white px-3.5 py-2 text-xs font-semibold text-[#0d6efd] shadow-sm transition hover:bg-[#0d6efd] hover:text-white"
                >
                  Chọn: {ctx.label}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    );
  }

  if (isDenied || isInsufficient || isNeedsAttention) {
    return (
      <div className="flex items-start gap-3">
        <div
          className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-xl ${
            isDenied
              ? "bg-[#fff0ed] text-[#a84839]"
              : isInsufficient
                ? "bg-[#fff7e5] text-[#9a6a16]"
                : "bg-[#eef2f6] text-[#536579]"
          }`}
        >
          {isDenied ? <ShieldIcon className="h-4 w-4" /> : <AlertIcon className="h-4 w-4" />}
        </div>
        <div
          className={`max-w-2xl rounded-2xl border p-5 ${
            isDenied
              ? "border-[#f0d7d2] bg-[#fffaf9]"
              : isInsufficient
                ? "border-[#eadfbd] bg-[#fffdf8]"
                : "border-[#d8e0e9] bg-[#f9fbfd]"
          }`}
        >
          <p className="text-[11px] font-bold uppercase tracking-[0.13em] text-[#7f675f]">
            {isDenied
              ? " · Không tìm thấy trong phạm vi của bạn"
              : isInsufficient
                ? " · Chưa đủ căn cứ chứng minh để trả lời"
                : " · Dữ liệu hoặc hệ thống cần xử lý thêm"}
          </p>
          <h2 className="mt-2 text-lg font-semibold leading-7 tracking-[-0.02em]">
            {answer.content}
          </h2>
          {answer.details?.map((detail) => (
            <p className="mt-3 text-sm leading-6 text-[#6d746e]" key={detail}>
              {detail}
            </p>
          ))}
          <div className="mt-5 flex items-center gap-3">
            <button
              className="rounded-xl border border-[#d6dcd7] bg-white px-4 py-2.5 text-xs font-bold text-[#3f4d43] transition hover:border-[#b9c5bb] disabled:opacity-50"
              type="button"
              disabled={feedbackLoading || feedbackSent}
              onClick={() => void handleAskOwner()}
            >
              {feedbackSent
                ? "✓ Đã gửi tới người phụ trách"
                : feedbackLoading
                  ? "Đang gửi..."
                  : "Hỏi người phụ trách"}
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex items-start gap-3">
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-[#165c3a] text-white">
        <SparklesIcon className="h-4 w-4" />
      </div>
      <article className="min-w-0 max-w-2xl flex-1">
        <div className="mb-3 flex items-center gap-2">
          <span className="text-xs font-bold">Appota Knowledge</span>
          <span className="rounded-full bg-[#eaf4ed] px-2 py-0.5 text-[9px] font-bold uppercase tracking-wider text-[#34704d]">
            Đã kiểm chứng
          </span>
          <span className="text-[10px] text-[#9ba19c]">{answer.answeredAt}</span>
        </div>
        <h2 className="text-xl font-semibold leading-7 tracking-[-0.025em]">{answer.title}</h2>
        
        {isEscalation && (
          <div className="mt-3 flex items-center gap-2 rounded-xl border border-[#f3e3be] bg-[#fffcf2] px-3.5 py-2 text-xs font-medium text-[#8a5d14]">
            <AlertIcon className="h-4 w-4 shrink-0" />
            <span>Chủ đề cần xác nhận thêm với <b>{answer.escalateTo || "Knowledge Owner / Quản lý"}</b>.</span>
          </div>
        )}

        <p className="mt-3 text-sm leading-7 text-[#4f5952]">{answer.content}</p>

        {answer.details && (
          <ul className="mt-4 space-y-2">
            {answer.details.map((detail) => (
              <li className="flex gap-2 text-[13px] leading-6 text-[#687169]" key={detail}>
                <span className="mt-2.5 h-1 w-1 shrink-0 rounded-full bg-[#659476]" />
                {detail}
              </li>
            ))}
          </ul>
        )}

        {isEscalation && (
          <div className="mt-4">
            <button
              className="rounded-xl border border-[#d6dcd7] bg-white px-3.5 py-2 text-xs font-bold text-[#3f4d43] shadow-sm transition hover:border-[#b9c5bb] disabled:opacity-50"
              type="button"
              disabled={feedbackLoading || feedbackSent}
              onClick={() => void handleAskOwner()}
            >
              {feedbackSent
                ? "✓ Đã gửi yêu cầu xác nhận tới người phụ trách"
                : feedbackLoading
                  ? "Đang gửi..."
                  : `Hỏi ${answer.escalateTo || "người phụ trách"}`}
            </button>
          </div>
        )}

        {answer.sources && answer.sources.length > 0 && (
          <div className="mt-6">
            <div className="mb-3 flex items-center gap-2">
              <BookIcon className="h-4 w-4 text-[#407254]" />
              <p className="text-xs font-bold">
                Nguồn trích dẫn
                <span className="ml-1.5 font-medium text-[#8a918b]">({answer.sources.length})</span>
              </p>
            </div>
            <div className="space-y-2">
              {answer.sources.map((source, index) => (
                <SourceCard index={index + 1} key={source.id} source={source} />
              ))}
            </div>
          </div>
        )}
      </article>
    </div>
  );
}

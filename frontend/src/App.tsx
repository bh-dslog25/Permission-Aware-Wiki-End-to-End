import { FormEvent, useEffect, useState } from "react";
import ChatPage from "./components/ChatPage";
import { authenticate, fetchPersonas, type Persona, type User } from "./api";
import { ArrowRightIcon, LockIcon, SparklesIcon } from "./components/Icons";

function LoginPage({ onLogin }: { onLogin: (user: User) => void }) {
  const [accounts, setAccounts] = useState<Persona[]>([]);
  const [selectedAccountId, setSelectedAccountId] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    void fetchPersonas().then((list) => {
      setAccounts(list);
      if (list.length > 0) {
        setSelectedAccountId(list[0].id);
        setEmail(list[0].email);
      }
    });
  }, []);

  useEffect(() => {
    const selected = accounts.find((account) => account.id === selectedAccountId);
    if (selected) {
      setEmail(selected.email);
    }
  }, [selectedAccountId, accounts]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setIsLoading(true);

    try {
      const user = await authenticate(email, password);
      onLogin(user);
    } catch (loginError) {
      setError(loginError instanceof Error ? loginError.message : "Không thể đăng nhập.");
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <main className="relative flex min-h-screen overflow-hidden bg-[#f7f8f6] text-[#1c211d]">
      <div className="pointer-events-none absolute -left-40 top-[-18rem] h-[44rem] w-[44rem] rounded-full bg-[#dfeee5] blur-3xl" />
      <div className="pointer-events-none absolute -bottom-80 right-[-14rem] h-[48rem] w-[48rem] rounded-full bg-[#f5ead0] blur-3xl" />

      <section className="relative mx-auto flex w-full max-w-7xl flex-col px-6 py-7 lg:px-10">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-[#165c3a] text-white shadow-sm">
            <SparklesIcon className="h-5 w-5" />
          </div>
          <div>
            <p className="text-[15px] font-bold tracking-[-0.02em]">Appota Knowledge</p>
            <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[#788078]">
              Internal workspace
            </p>
          </div>
        </div>

        <div className="flex flex-1 items-center justify-center py-12">
          <div className="grid w-full max-w-5xl items-center gap-16 lg:grid-cols-[1.05fr_0.95fr]">
            <div className="hidden lg:block">
              <div className="mb-7 inline-flex items-center gap-2 rounded-full border border-[#d9dfd9] bg-white/70 px-3 py-1.5 text-xs font-semibold text-[#536058]">
                <LockIcon className="h-3.5 w-3.5 text-[#28734d]" />
                Tri thức đúng quyền, đúng ngữ cảnh
              </div>
              <h1 className="max-w-xl text-5xl font-semibold leading-[1.08] tracking-[-0.045em] text-[#172019]">
                Tri thức nội bộ, dành riêng cho bạn.
              </h1>
              <p className="mt-6 max-w-lg text-lg leading-8 text-[#626b64]">
                Hỏi bằng ngôn ngữ tự nhiên. Nhận câu trả lời theo đúng đơn vị, phiên bản
                hiệu lực và quyền truy cập của bạn.
              </p>
              <div className="mt-10 flex gap-8 border-t border-[#dde2dd] pt-6">
                <div>
                  <p className="text-2xl font-semibold tracking-tight">100%</p>
                  <p className="mt-1 text-sm text-[#717871]">Có nguồn trích dẫn</p>
                </div>
                <div>
                  <p className="text-2xl font-semibold tracking-tight">0</p>
                  <p className="mt-1 text-sm text-[#717871]">Dữ liệu ngoài quyền</p>
                </div>
              </div>
            </div>

            <div className="rounded-[28px] border border-white/80 bg-white/85 p-2 shadow-[0_24px_80px_rgba(28,45,34,0.12)] backdrop-blur">
              <form
                className="rounded-3xl border border-[#e3e7e2] bg-white p-7 sm:p-9"
                onSubmit={handleSubmit}
              >
                <div className="mb-8">
                  <p className="mb-2 text-xs font-bold uppercase tracking-[0.16em] text-[#28734d]">
                    Đăng nhập bảo mật
                  </p>
                  <h2 className="text-3xl font-semibold tracking-[-0.035em]">Chào mừng trở lại</h2>
                  <p className="mt-2 text-sm leading-6 text-[#747b75]">
                    Sử dụng tài khoản công ty để tiếp tục.
                  </p>
                </div>

                <label className="mb-5 block">
                  <span className="mb-2 block text-sm font-semibold text-[#3c453e]">
                    Chọn tài khoản
                  </span>
                  <select
                    className="h-12 w-full rounded-xl border border-[#d8ded8] bg-[#fafbf9] px-4 text-[15px] outline-none transition focus:border-[#28734d] focus:bg-white focus:ring-4 focus:ring-[#28734d]/10"
                    value={selectedAccountId}
                    onChange={(event) => setSelectedAccountId(event.target.value)}
                  >
                    {accounts.map((account) => (
                      <option key={account.id} value={account.id}>
                        {account.name} · {account.title || account.description}
                      </option>
                    ))}
                  </select>
                </label>

                <label className="mb-5 block">
                  <span className="mb-2 block text-sm font-semibold text-[#3c453e]">
                    Email công việc
                  </span>
                  <input
                    className="h-12 w-full rounded-xl border border-[#d8ded8] bg-[#fafbf9] px-4 text-[15px] outline-none transition placeholder:text-[#a4aaa4] focus:border-[#28734d] focus:bg-white focus:ring-4 focus:ring-[#28734d]/10"
                    onChange={(event) => setEmail(event.target.value)}
                    type="email"
                    value={email}
                  />
                </label>

                <label className="block">
                  <span className="mb-2 block text-sm font-semibold text-[#3c453e]">Mật khẩu</span>
                  <input
                    className="h-12 w-full rounded-xl border border-[#d8ded8] bg-[#fafbf9] px-4 text-[15px] outline-none transition placeholder:text-[#a4aaa4] focus:border-[#28734d] focus:bg-white focus:ring-4 focus:ring-[#28734d]/10"
                    onChange={(event) => setPassword(event.target.value)}
                    type="password"
                    value={password}
                  />
                </label>

                {error && (
                  <p className="mt-4 rounded-xl bg-[#fff1ee] px-4 py-3 text-sm text-[#a33c2f]">
                    {error}
                  </p>
                )}

                <button
                  className="mt-6 flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-[#165c3a] px-5 text-sm font-bold text-white shadow-[0_8px_24px_rgba(22,92,58,0.2)] transition hover:bg-[#0f4e30] focus:outline-none focus:ring-4 focus:ring-[#165c3a]/20 disabled:cursor-wait disabled:opacity-70"
                  disabled={isLoading}
                  type="submit"
                >
                  {isLoading ? "Đang xác thực..." : "Đăng nhập"}
                  {!isLoading && <ArrowRightIcon className="h-4 w-4" />}
                </button>

                <div className="my-6 flex items-center gap-3">
                  <div className="h-px flex-1 bg-[#e7e9e6]" />
                  <span className="text-[11px] font-semibold uppercase tracking-wider text-[#979d97]">
                    Demo nội bộ
                  </span>
                  <div className="h-px flex-1 bg-[#e7e9e6]" />
                </div>

                <p className="text-center text-xs leading-5 text-[#8a918b]">
                  Tài khoản và quyền truy cập được quản lý trong cơ sở dữ liệu.
                </p>
              </form>
            </div>
          </div>
        </div>
      </section>
    </main>
  );
}

import OwnerReviewPortal from "./components/OwnerReviewPortal";
import AdminPortal from "./components/AdminPortal";

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [currentView, setCurrentView] = useState<"chat" | "owner" | "admin">("chat");

  if (!user) {
    return <LoginPage onLogin={(u) => {
      setUser(u);
      if (u.role === "security") {
        setCurrentView("admin");
      } else if (u.role === "hr_owner") {
        setCurrentView("owner");
      } else {
        setCurrentView("chat");
      }
    }} />;
  }

  if (currentView === "admin") {
    return (
      <AdminPortal
        user={user}
        onBackToChat={() => setCurrentView("chat")}
        onLogout={() => {
          setUser(null);
          setCurrentView("chat");
        }}
      />
    );
  }

  if (currentView === "owner") {
    return (
      <OwnerReviewPortal
        user={user}
        onBackToChat={() => setCurrentView("chat")}
        onLogout={() => {
          setUser(null);
          setCurrentView("chat");
        }}
      />
    );
  }

  return (
    <ChatPage
      onLogout={() => {
        setUser(null);
        setCurrentView("chat");
      }}
      user={user}
      onOpenOwnerPortal={() => setCurrentView("owner")}
      onOpenAdminPortal={() => setCurrentView("admin")}
    />
  );
}

export type ContextOption = {
  id: string;
  label: string;
};

export type User = {
  id: string;
  name: string;
  email: string;
  title: string;
  initials: string;
  role: string;
  clearance: string;
  description: string;
  contexts: ContextOption[];
  defaultContextId: string;
};

export type Persona = {
  id: string;
  name: string;
  email: string;
  title: string;
  role: string;
  clearance: string;
  description: string;
  contexts: ContextOption[];
  defaultContextId: string;
  initials: string;
};

export type Source = {
  id: string;
  title: string;
  section: string;
  excerpt: string;
  version: string;
  effectiveDate: string;
  owner: string;
};

export type AnswerStatus =
  | "answered"
  | "denied"
  | "insufficient"
  | "needs_attention"
  | "needs_clarification"
  | "answer_with_escalation";
export type PublicRefusalCode = "A" | "B" | "C+D";

export type ChatAnswer = {
  id: string;
  conversationId?: string;
  status: AnswerStatus;
  title?: string;
  content: string;
  details?: string[];
  sources: Source[];
  answeredAt: string;
  availableContexts?: ContextOption[];
  escalateTo?: string | null;
};

type BackendCitation = {
  index: number;
  document_id: string;
  chunk_id: string;
  title: string;
  excerpt: string;
  quote_verified: boolean;
  version_number?: string | null;
};

type BackendAnswer = {
  status: "answered" | "abstained" | "needs_clarification" | "answer_with_escalation";
  answer: string;
  applies_to?: string | null;
  context_label?: string | null;
  effective_at?: string | null;
  as_of?: string | null;
  citations: BackendCitation[];
  refused: boolean;
  refusal_reason?: PublicRefusalCode | null;
  escalate_to?: string | null;
  available_contexts?: ContextOption[];
  request_id: string;
  conversation_id?: string | null;
};

export type ConversationSummary = {
  id: string;
  title: string;
  context_id: string;
  created_at: string;
  updated_at: string;
};

export type ConversationDetail = ConversationSummary & {
  messages: Array<{
    id: string;
    question: string;
    answer: BackendAnswer;
  }>;
};

type BackendPersona = {
  id: string;
  name: string;
  email: string;
  title: string;
  role: string;
  clearance: string;
  description: string;
  contexts: Array<{ id: string; label: string }>;
  default_context_id?: string | null;
  is_persona?: boolean;
};

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

function getInitials(name: string): string {
  const parts = name.trim().split(/\s+/);
  if (parts.length >= 2) {
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  }
  return name.slice(0, 2).toUpperCase();
}

export function toPersona(backendP: BackendPersona): Persona {
  return {
    id: backendP.id,
    name: backendP.name,
    email: backendP.email,
    title: backendP.title,
    role: backendP.role,
    clearance: backendP.clearance,
    description: backendP.description,
    contexts: backendP.contexts || [],
    defaultContextId: backendP.default_context_id || (backendP.contexts?.[0]?.id ?? ""),
    initials: getInitials(backendP.name),
  };
}

export function toUser(backendP: BackendPersona): User {
  return {
    id: backendP.id,
    name: backendP.name,
    email: backendP.email,
    title: backendP.title,
    role: backendP.role,
    clearance: backendP.clearance,
    description: backendP.description,
    contexts: backendP.contexts || [],
    defaultContextId: backendP.default_context_id || (backendP.contexts?.[0]?.id ?? ""),
    initials: getInitials(backendP.name),
  };
}

export async function fetchPersonas(): Promise<Persona[]> {
  try {
    const res = await fetch(`${API_BASE_URL}/api/v1/personas`);
    if (!res.ok) {
      throw new Error("Không thể tải danh sách tài khoản từ máy chủ.");
    }
    const data = (await res.json()) as BackendPersona[];
    return data.map(toPersona);
  } catch (err) {
    console.warn("fetchPersonas error, using fallback accounts:", err);
    return [
      {
        id: "u_minh_anh",
        name: "Nguyễn Minh Anh",
        email: "minh.anh@appota.com",
        title: "Chuyên viên Kinh doanh",
        role: "employee",
        clearance: "internal",
        description: "Nhân viên Kinh doanh, Công ty A",
        contexts: [{ id: "ctx_minh_anh_main", label: "Nhóm Kinh doanh 1" }],
        defaultContextId: "ctx_minh_anh_main",
        initials: "MA",
      },
      {
        id: "u_quoc_bao",
        name: "Trần Quốc Bảo",
        email: "quoc.bao@appota.com",
        title: "Kỹ sư Backend",
        role: "employee",
        clearance: "internal",
        description: "Nhân viên Kỹ thuật, Công ty B",
        contexts: [{ id: "ctx_quoc_bao_main", label: "Nhóm Backend" }],
        defaultContextId: "ctx_quoc_bao_main",
        initials: "QB",
      },
      {
        id: "u_thu_ha",
        name: "Lê Thu Hà",
        email: "thu.ha@appota.com",
        title: "Trưởng phòng Kinh doanh – Công ty A",
        role: "manager",
        clearance: "confidential",
        description: "Quản lý kiêm nhiệm: Trưởng phòng Kinh doanh Công ty A và quản lý nhóm QA Công ty B",
        contexts: [
          { id: "ctx_thu_ha_a", label: "Nhóm Kinh doanh 2 (Công ty A)" },
          { id: "ctx_thu_ha_b", label: "Nhóm QA (Công ty B)" },
        ],
        defaultContextId: "ctx_thu_ha_a",
        initials: "TH",
      },
      {
        id: "u_pham_lan",
        name: "Phạm Lan",
        email: "pham.lan@appota.com",
        title: "HR Business Partner",
        role: "hr_owner",
        clearance: "confidential",
        description: "Knowledge Owner / HR Công ty A (duyệt metadata, supersede)",
        contexts: [{ id: "ctx_pham_lan_main", label: "Phòng Nhân sự (Công ty A)" }],
        defaultContextId: "ctx_pham_lan_main",
        initials: "PL",
      },
    ];
  }
}

export async function authenticate(identifier: string, password: string): Promise<User> {
  const cleanId = identifier.trim();
  const cleanPass = password.trim();

  if (!cleanId) {
    throw new Error("Vui lòng nhập email hoặc mã tài khoản.");
  }

  const response = await fetch(`${API_BASE_URL}/api/v1/auth/login`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      username_or_email: cleanId,
      password: cleanPass,
    }),
  });

  if (!response.ok) {
    const errorBody = await response.json().catch(() => ({}));
    throw new Error(
      errorBody.detail || "Đăng nhập không thành công. Hãy kiểm tra tài khoản trong hệ thống.",
    );
  }

  const result = (await response.json()) as { status: string; user: BackendPersona };
  return toUser(result.user);
}

export type AdminRole = { role_code: string; role_name: string };
export type AdminUnit = { unit_code: string; name: string };
export type AdminOrganizationUnitType =
  | "group"
  | "company"
  | "division"
  | "department"
  | "team"
  | "project"
  | "committee";
export type AdminOrganizationUnit = {
  unit_code: string;
  name: string;
  unit_type: AdminOrganizationUnitType;
  status: "active" | "inactive";
  parent_unit_code: string | null;
  parent_name?: string | null;
};
export type AdminMembership = {
  membership_id: string;
  context_id: string;
  membership_type: "primary" | "matrix" | "project";
  status: "active" | "inactive";
  valid_from: string | null;
  valid_to: string | null;
  job_title: string;
  unit_code: string;
  unit_name: string;
  roles: Array<{ role_code: string; role_name: string; status: string }>;
};
export type AdminUser = {
  user_code: string;
  display_name: string;
  email: string;
  status: "active" | "disabled";
  memberships: AdminMembership[];
};
export type AdminDirectory = {
  items: AdminUser[];
  units: AdminUnit[];
  roles: AdminRole[];
};

async function adminRequest<T>(
  path: string,
  userId: string,
  init: RequestInit = {},
): Promise<T> {
  const response = await fetch(`${API_BASE_URL}/api/v1/admin${path}`, {
    ...init,
    headers: {
      "X-Persona-Id": userId,
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...init.headers,
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ?? "Không thể thực hiện thao tác quản trị.");
  }
  return (await response.json()) as T;
}

export function fetchAdminDirectory(userId: string): Promise<AdminDirectory> {
  return adminRequest<AdminDirectory>("/users", userId);
}

export async function fetchAdminOrganizationUnits(
  userId: string,
): Promise<AdminOrganizationUnit[]> {
  const result = await adminRequest<{ items: AdminOrganizationUnit[] }>(
    "/organization-units",
    userId,
  );
  return result.items;
}

export function createAdminOrganizationUnit(
  userId: string,
  payload: {
    unit_code: string;
    name: string;
    unit_type: AdminOrganizationUnitType;
    parent_unit_code: string | null;
  },
): Promise<AdminOrganizationUnit> {
  return adminRequest("/organization-units", userId, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateAdminOrganizationUnit(
  userId: string,
  unitCode: string,
  payload: {
    name: string;
    parent_unit_code: string | null;
    status: "active" | "inactive";
  },
): Promise<AdminOrganizationUnit> {
  return adminRequest(
    `/organization-units/${encodeURIComponent(unitCode)}`,
    userId,
    { method: "PATCH", body: JSON.stringify(payload) },
  );
}

export function createAdminUser(
  userId: string,
  payload: {
    user_code: string;
    display_name: string;
    email: string;
    password: string;
    context_id: string;
    unit_code: string;
    role_code: string;
    job_title: string;
    membership_type: "primary" | "matrix" | "project";
  },
): Promise<{ user_code: string }> {
  return adminRequest("/users", userId, { method: "POST", body: JSON.stringify(payload) });
}

export function setAdminUserStatus(
  userId: string,
  targetUserCode: string,
  status: "active" | "disabled",
): Promise<{ user_code: string; status: string }> {
  return adminRequest(`/users/${encodeURIComponent(targetUserCode)}`, userId, {
    method: "PATCH",
    body: JSON.stringify({ status }),
  });
}

export function addAdminMembership(
  userId: string,
  targetUserCode: string,
  payload: {
    context_id: string;
    unit_code: string;
    role_code: string;
    job_title: string;
    membership_type: "primary" | "matrix" | "project";
  },
): Promise<{ membership_id: string }> {
  return adminRequest(
    `/users/${encodeURIComponent(targetUserCode)}/memberships`,
    userId,
    { method: "POST", body: JSON.stringify(payload) },
  );
}

export function changeAdminMembershipRole(
  userId: string,
  membershipId: string,
  roleCode: string,
): Promise<{ membership_id: string; role_code: string }> {
  return adminRequest(`/memberships/${encodeURIComponent(membershipId)}`, userId, {
    method: "PATCH",
    body: JSON.stringify({ role_code: roleCode }),
  });
}

export function revokeAdminMembership(
  userId: string,
  membershipId: string,
): Promise<{ membership_id: string }> {
  return adminRequest(`/memberships/${encodeURIComponent(membershipId)}`, userId, {
    method: "DELETE",
  });
}

function formatTime() {
  return new Intl.DateTimeFormat("vi-VN", {
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date());
}

function formatDate(value?: string | null) {
  if (!value) return "không rõ";
  return new Intl.DateTimeFormat("vi-VN").format(new Date(`${value}T00:00:00`));
}

function statusOf(answer: BackendAnswer): AnswerStatus {
  if (answer.status === "answered") return "answered";
  if (answer.status === "needs_clarification") return "needs_clarification";
  if (answer.status === "answer_with_escalation") return "answer_with_escalation";
  if (answer.refusal_reason === "A") return "denied";
  if (answer.refusal_reason === "B") return "insufficient";
  return "needs_attention";
}

export async function askQuestion(
  question: string,
  userId: string,
  contextId: string,
  conversationId?: string,
): Promise<ChatAnswer> {
  const response = await fetch(`${API_BASE_URL}/api/v1/ask`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Persona-Id": userId,
    },
    body: JSON.stringify({
      question,
      context_id: contextId,
      ...(conversationId ? { conversation_id: conversationId } : {}),
    }),
  });

  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ?? "Không gọi được backend hỏi đáp.");
  }

  const answer = (await response.json()) as BackendAnswer;
  return toChatAnswer(answer);
}

function toChatAnswer(answer: BackendAnswer): ChatAnswer {
  const sources = answer.citations.map((citation) => ({
    id: citation.chunk_id,
    title: citation.title,
    section: citation.chunk_id,
    excerpt: citation.excerpt,
    version: citation.version_number ? `Phiên bản ${citation.version_number}` : "Phiên bản không rõ",
    effectiveDate: formatDate(answer.effective_at ?? answer.as_of),
    owner: answer.applies_to ?? answer.context_label ?? "Nguồn được cấp quyền",
  }));

  const details = [
    answer.applies_to ? `Phạm vi áp dụng: ${answer.applies_to}.` : null,
    answer.as_of ? `Tra cứu tại thời điểm: ${formatDate(answer.as_of)}.` : null,
    answer.request_id ? `Request ID: ${answer.request_id}.` : null,
  ].filter(Boolean) as string[];

  let title: string | undefined = undefined;
  if (answer.status === "answered") {
    title = "Câu trả lời dựa trên nguồn được phép";
  } else if (answer.status === "needs_clarification") {
    title = "Yêu cầu chọn ngữ cảnh làm việc";
  } else if (answer.status === "answer_with_escalation") {
    title = `Cần xác nhận với ${answer.escalate_to ?? "Knowledge Owner"}`;
  }

  return {
    id: answer.request_id,
    conversationId: answer.conversation_id ?? undefined,
    status: statusOf(answer),
    title,
    content: answer.answer,
    details,
    sources,
    answeredAt: formatTime(),
    availableContexts: answer.available_contexts,
    escalateTo: answer.escalate_to,
  };
}

export async function fetchConversations(userId: string): Promise<ConversationSummary[]> {
  const response = await fetch(`${API_BASE_URL}/api/v1/conversations`, {
    headers: { "X-Persona-Id": userId },
  });
  if (!response.ok) {
    throw new Error("Không tải được lịch sử hội thoại.");
  }
  return (await response.json()) as ConversationSummary[];
}

export async function fetchConversation(
  userId: string,
  conversationId: string,
): Promise<ConversationDetail> {
  const response = await fetch(`${API_BASE_URL}/api/v1/conversations/${encodeURIComponent(conversationId)}`, {
    headers: { "X-Persona-Id": userId },
  });
  if (!response.ok) {
    throw new Error("Không tải được nội dung cuộc trò chuyện.");
  }
  return (await response.json()) as ConversationDetail;
}

export function toConversationEntry(entry: ConversationDetail["messages"][number]) {
  return {
    id: entry.id,
    question: entry.question,
    answer: toChatAnswer(entry.answer),
  };
}

export async function submitFeedback(
  requestId: string,
  contextId: string,
  content: string = "",
  escalateTo?: string | null,
  personaId?: string,
): Promise<{ status: string; message: string }> {
  const response = await fetch(`${API_BASE_URL}/api/v1/feedback`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(personaId ? { "X-Persona-Id": personaId } : {}),
    },
    body: JSON.stringify({
      request_id: requestId,
      context_id: contextId,
      content,
      escalate_to: escalateTo || "Knowledge Owner",
    }),
  });

  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ?? "Không gửi được phản hồi.");
  }

  return await response.json();
}

export type PendingReviewItem = {
  document_id: string;
  title: string;
  doc_type: string;
  owner_unit: string;
  owner_unit_name: string;
  scope: string;
  classification: number;
  version: string;
  valid_from: string;
  valid_to: string | null;
  status: string;
  mandatory: boolean;
  approved: boolean;
  topic: string | null;
  parent_conformance: "confirmed" | "unverified" | "conflicting";
  source_path: string;
  updated_at?: string;
  last_review?: {
    doc_id: string;
    user_id: string;
    ts: string;
    decision: "confirmed" | "conflicting" | "unverified";
    justification: string;
    clause: string;
  } | null;
  chunks: Array<{
    chunk_id: string;
    chunk_index: number;
    section_path: string | null;
    content: string;
  }>;
  parent_policy?: {
    document_id: string;
    title: string;
    owner_unit: string;
    owner_unit_name: string;
    scope: string;
    version: string;
    mandatory: boolean;
    topic: string;
    chunks: Array<{
      chunk_id: string;
      chunk_index: number;
      section_path: string | null;
      content: string;
    }>;
  } | null;
};

export async function fetchPendingReviews(
  personaId: string,
  status: "pending" | "reviewed" | "all" = "pending",
): Promise<{
  owner: { id: string; name: string; role: string };
  stats?: { pending: number; reviewed: number; total: number };
  items: PendingReviewItem[];
}> {
  const response = await fetch(`${API_BASE_URL}/api/v1/owner/pending-reviews?status=${status}`, {
    headers: {
      "X-Persona-Id": personaId,
    },
  });

  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ?? "Không thể tải danh sách tài liệu.");
  }

  return await response.json();
}

export async function submitDocumentReview(
  documentId: string,
  personaId: string,
  payload: {
    decision: "confirmed" | "conflicting" | "unverified";
    justification: string;
    clause: string;
  },
): Promise<{ status: string; message: string; new_conformance: string }> {
  const response = await fetch(`${API_BASE_URL}/api/v1/owner/reviews/${documentId}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Persona-Id": personaId,
    },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ?? "Không thể gửi kết quả phê duyệt.");
  }

  return await response.json();
}

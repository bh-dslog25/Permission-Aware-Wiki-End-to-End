import { FormEvent, useCallback, useEffect, useState } from "react";
import {
  addAdminMembership,
  changeAdminMembershipRole,
  createAdminOrganizationUnit,
  createAdminUser,
  fetchAdminDirectory,
  fetchAdminOrganizationUnits,
  revokeAdminMembership,
  updateAdminOrganizationUnit,
  setAdminUserStatus,
  type AdminDirectory,
  type AdminMembership,
  type AdminOrganizationUnit,
  type AdminOrganizationUnitType,
  type AdminUser,
  type User,
} from "../api";
import { LogOutIcon, ShieldIcon, SparklesIcon } from "./Icons";

type Props = {
  user: User;
  onBackToChat: () => void;
  onLogout: () => void;
};

type MembershipForm = {
  context_id: string;
  unit_code: string;
  role_code: string;
  job_title: string;
  membership_type: "primary" | "matrix" | "project";
};

type OrganizationUnitForm = {
  unit_code: string;
  name: string;
  unit_type: AdminOrganizationUnitType;
  parent_unit_code: string;
  status: "active" | "inactive";
};

const unitTypeLabels: Record<AdminOrganizationUnitType, string> = {
  group: "Tập đoàn / Group",
  company: "Công ty",
  division: "Khối / Division",
  department: "Phòng ban / Department",
  team: "Nhóm / Team",
  project: "Dự án",
  committee: "Ủy ban",
};
const allowedParentTypes: Record<AdminOrganizationUnitType, AdminOrganizationUnitType[]> = {
  group: [],
  company: ["group"],
  division: ["company"],
  department: ["company", "division"],
  team: ["department"],
  project: ["group", "company", "division", "department", "team"],
  committee: ["group", "company", "division", "department", "team"],
};

function flattenOrganizationUnits(
  units: AdminOrganizationUnit[],
): Array<{ unit: AdminOrganizationUnit; depth: number }> {
  const childrenByParent = new Map<string | null, AdminOrganizationUnit[]>();
  for (const unit of units) {
    const siblings = childrenByParent.get(unit.parent_unit_code) ?? [];
    siblings.push(unit);
    childrenByParent.set(unit.parent_unit_code, siblings);
  }
  for (const siblings of childrenByParent.values()) {
    siblings.sort((left, right) => left.name.localeCompare(right.name));
  }

  const result: Array<{ unit: AdminOrganizationUnit; depth: number }> = [];
  const visited = new Set<string>();
  const visit = (parentCode: string | null, depth: number) => {
    for (const unit of childrenByParent.get(parentCode) ?? []) {
      if (visited.has(unit.unit_code)) continue;
      visited.add(unit.unit_code);
      result.push({ unit, depth });
      visit(unit.unit_code, depth + 1);
    }
  };
  visit(null, 0);
  for (const unit of units) {
    if (!visited.has(unit.unit_code)) {
      visited.add(unit.unit_code);
      result.push({ unit, depth: 0 });
      visit(unit.unit_code, 1);
    }
  }
  return result;
}

const emptyDirectory: AdminDirectory = { items: [], units: [], roles: [] };
const emptyOrganizationUnit: OrganizationUnitForm = {
  unit_code: "",
  name: "",
  unit_type: "company",
  parent_unit_code: "",
  status: "active",
};
const emptyMembership: MembershipForm = {
  context_id: "",
  unit_code: "",
  role_code: "",
  job_title: "",
  membership_type: "primary",
};
const inputClass =
  "mt-1 h-10 w-full rounded-lg border border-[#d8ded8] bg-white px-3 text-sm outline-none focus:border-[#28734d] focus:ring-2 focus:ring-[#28734d]/10";

export default function AdminPortal({ user, onBackToChat, onLogout }: Props) {
  const [directory, setDirectory] = useState<AdminDirectory>(emptyDirectory);
  const [organizationUnits, setOrganizationUnits] = useState<AdminOrganizationUnit[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [search, setSearch] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [unitDialog, setUnitDialog] = useState<AdminOrganizationUnit | "create" | null>(null);
  const [unitForm, setUnitForm] = useState<OrganizationUnitForm>(emptyOrganizationUnit);
  const [membershipFor, setMembershipFor] = useState<string | null>(null);
  const [newUser, setNewUser] = useState({
    user_code: "",
    display_name: "",
    email: "",
    password: "",
    ...emptyMembership,
  });
  const [newMembership, setNewMembership] = useState<MembershipForm>(emptyMembership);

  const loadAdminData = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [nextDirectory, nextUnits] = await Promise.all([
        fetchAdminDirectory(user.id),
        fetchAdminOrganizationUnits(user.id),
      ]);
      setDirectory(nextDirectory);
      setOrganizationUnits(nextUnits);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Không tải được dữ liệu quản trị.");
    } finally {
      setLoading(false);
    }
  }, [user.id]);

  useEffect(() => {
    void loadAdminData();
  }, [loadAdminData]);

  useEffect(() => {
    if (directory.units.length && !newUser.unit_code) {
      setNewUser((value) => ({ ...value, unit_code: directory.units[0].unit_code }));
    }
    if (directory.roles.length && !newUser.role_code) {
      const defaultRole =
        directory.roles.find((role) => role.role_code === "employee")?.role_code ??
        directory.roles[0].role_code;
      setNewUser((value) => ({ ...value, role_code: defaultRole }));
    }
    if (directory.units.length && !newMembership.unit_code) {
      setNewMembership((value) => ({ ...value, unit_code: directory.units[0].unit_code }));
    }
    if (directory.roles.length && !newMembership.role_code) {
      const defaultRole =
        directory.roles.find((role) => role.role_code === "employee")?.role_code ??
        directory.roles[0].role_code;
      setNewMembership((value) => ({ ...value, role_code: defaultRole }));
    }
  }, [directory, newUser.unit_code, newUser.role_code, newMembership.unit_code, newMembership.role_code]);

  async function runAction(action: () => Promise<unknown>, successMessage: string): Promise<boolean> {
    setSaving(true);
    setError("");
    setNotice("");
    try {
      await action();
      await loadAdminData();
      setNotice(successMessage);
      return true;
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : "Thao tác thất bại.");
      return false;
    } finally {
      setSaving(false);
    }
  }

  async function handleCreateUser(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const succeeded = await runAction(
      () => createAdminUser(user.id, { ...newUser }),
      `Đã tạo tài khoản ${newUser.user_code}.`,
    );
    if (!succeeded) return;
    setCreateOpen(false);
    setNewUser({
      user_code: "",
      display_name: "",
      email: "",
      password: "",
      ...emptyMembership,
      unit_code: directory.units[0]?.unit_code ?? "",
      role_code: directory.roles.find((role) => role.role_code === "employee")?.role_code ?? "",
    });
  }

  async function handleAddMembership(event: FormEvent<HTMLFormElement>, target: AdminUser) {
    event.preventDefault();
    const succeeded = await runAction(
      () => addAdminMembership(user.id, target.user_code, newMembership),
      `Đã cấp thêm quyền cho ${target.display_name}.`,
    );
    if (!succeeded) return;
    setMembershipFor(null);
    setNewMembership({
      ...emptyMembership,
      unit_code: directory.units[0]?.unit_code ?? "",
      role_code: directory.roles.find((role) => role.role_code === "employee")?.role_code ?? "",
    });
  }

  function openCreateUnit() {
    setUnitForm({
      ...emptyOrganizationUnit,
      parent_unit_code: organizationUnits.find(
        (unit) => unit.unit_type === "group" && unit.status === "active",
      )?.unit_code ?? "",
    });
    setUnitDialog("create");
  }

  function openEditUnit(unit: AdminOrganizationUnit) {
    setUnitForm({
      unit_code: unit.unit_code,
      name: unit.name,
      unit_type: unit.unit_type,
      parent_unit_code: unit.parent_unit_code ?? "",
      status: unit.status,
    });
    setUnitDialog(unit);
  }

  async function handleSaveUnit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const isCreating = unitDialog === "create";
    const succeeded = await runAction(
      () =>
        isCreating
          ? createAdminOrganizationUnit(user.id, {
              unit_code: unitForm.unit_code,
              name: unitForm.name,
              unit_type: unitForm.unit_type,
              parent_unit_code: unitForm.parent_unit_code || null,
            })
          : updateAdminOrganizationUnit(user.id, unitForm.unit_code, {
              name: unitForm.name,
              parent_unit_code: unitForm.parent_unit_code || null,
              status: unitForm.status,
            }),
      isCreating
        ? `Đã tạo đơn vị ${unitForm.name}.`
        : `Đã cập nhật đơn vị ${unitForm.name}.`,
    );
    if (succeeded) setUnitDialog(null);
  }

  const orderedUnits = flattenOrganizationUnits(organizationUnits);
  const selectableParents = organizationUnits.filter(
    (unit) =>
      unit.status === "active" &&
      allowedParentTypes[unitForm.unit_type].includes(unit.unit_type) &&
      unit.unit_code !== unitForm.unit_code,
  );

  const filteredUsers = directory.items.filter((item) =>
    `${item.user_code} ${item.display_name} ${item.email}`
      .toLocaleLowerCase()
      .includes(search.toLocaleLowerCase()),
  );

  return (
    <div className="min-h-screen bg-[#f6f7f5] text-[#202721]">
      <header className="sticky top-0 z-10 flex h-[4.5rem] items-center justify-between border-b border-[#e1e5e1] bg-white px-4 sm:px-8">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-[#165c3a] text-white">
            <SparklesIcon className="h-[18px] w-[18px]" />
          </div>
          <div>
            <p className="text-sm font-bold">Appota Knowledge</p>
            <p className="text-[10px] font-semibold uppercase tracking-wider text-[#7b857d]">
              Quản trị tổ chức, tài khoản và phân quyền
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <span className="hidden text-sm font-semibold sm:inline">{user.name}</span>
          <button className="rounded-lg px-3 py-2 text-sm font-semibold text-[#326247] hover:bg-[#eff5f0]" onClick={onBackToChat} type="button">
            Về trò chuyện
          </button>
          <button aria-label="Đăng xuất" className="rounded-lg p-2 text-[#657168] hover:bg-[#f1f3f0]" onClick={onLogout} title="Đăng xuất" type="button">
            <LogOutIcon className="h-[18px] w-[18px]" />
          </button>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-4 py-8 sm:px-8">
        <div className="flex flex-col justify-between gap-5 sm:flex-row sm:items-end">
          <div>
            <div className="mb-3 inline-flex items-center gap-2 rounded-full bg-[#e8f3ed] px-3 py-1 text-xs font-bold text-[#276342]">
              <ShieldIcon className="h-4 w-4" />
              Chỉ dành cho Admin
            </div>
            <h1 className="text-3xl font-semibold tracking-tight">Tài khoản & quyền truy cập</h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-[#707a72]">
              Tạo tài khoản, cấp role theo đơn vị, thay đổi membership và khóa tài khoản nhân viên.
              Mọi thay đổi được ghi audit.
            </p>
          </div>
          <button
            className="h-11 rounded-xl bg-[#165c3a] px-5 text-sm font-bold text-white shadow-sm hover:bg-[#104e31] disabled:opacity-60"
            disabled={!directory.units.length || !directory.roles.length || saving}
            onClick={() => setCreateOpen(true)}
            type="button"
          >
            + Tạo tài khoản
          </button>
        </div>

        {(error || notice) && (
          <div className={`mt-6 rounded-xl border px-4 py-3 text-sm ${error ? "border-[#f0c9c1] bg-[#fff3f0] text-[#9b382c]" : "border-[#cfe4d5] bg-[#f0f8f2] text-[#276342]"}`}>
            {error || notice}
          </div>
        )}

        <section className="mt-7 overflow-hidden rounded-2xl border border-[#e0e5e0] bg-white shadow-sm">
          <div className="flex flex-col gap-3 border-b border-[#edf0ed] p-4 sm:flex-row sm:items-center sm:justify-between sm:px-6">
            <div>
              <h2 className="font-bold">Cây đơn vị tổ chức ({organizationUnits.length})</h2>
              <p className="mt-1 text-xs text-[#818a83]">
                Tạo đơn vị và cập nhật tên, đơn vị cha hoặc trạng thái. Mã và loại đơn vị không đổi sau khi tạo.
              </p>
            </div>
            <button
              className="h-10 rounded-lg bg-[#165c3a] px-4 text-sm font-bold text-white hover:bg-[#104e31] disabled:opacity-60"
              disabled={saving}
              onClick={openCreateUnit}
              type="button"
            >
              + Tạo đơn vị
            </button>
          </div>
          {loading ? (
            <p className="p-6 text-center text-sm text-[#7c857e]">Đang tải cây đơn vị...</p>
          ) : organizationUnits.length === 0 ? (
            <p className="p-6 text-center text-sm text-[#7c857e]">Chưa có organization unit.</p>
          ) : (
            <div className="divide-y divide-[#edf0ed]">
              {orderedUnits.map(({ unit, depth }) => (
                <div
                  className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6"
                  key={unit.unit_code}
                >
                  <div className="min-w-0" style={{ paddingLeft: `${depth * 18}px` }}>
                    <p className="truncate text-sm font-semibold text-[#27332a]">
                      {unit.name}
                      <span className="ml-2 font-normal text-[#7b857d]">{unit.unit_code}</span>
                    </p>
                    <p className="mt-1 text-xs text-[#818a83]">
                      {unitTypeLabels[unit.unit_type]}
                      {unit.parent_name ? ` · Thuộc ${unit.parent_name}` : " · Đơn vị gốc"}
                    </p>
                  </div>
                  <div className="flex items-center gap-3">
                    <span
                      className={`rounded-full px-2.5 py-1 text-xs font-semibold ${
                        unit.status === "active"
                          ? "bg-[#eaf5ed] text-[#276342]"
                          : "bg-[#f0f1ef] text-[#68736a]"
                      }`}
                    >
                      {unit.status === "active" ? "Đang hoạt động" : "Ngừng hoạt động"}
                    </span>
                    <button
                      className="rounded-lg border border-[#dce4dd] px-3 py-2 text-xs font-semibold text-[#326247] hover:bg-[#f4f8f4]"
                      disabled={saving}
                      onClick={() => openEditUnit(unit)}
                      type="button"
                    >
                      Chỉnh sửa
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="mt-7 overflow-hidden rounded-2xl border border-[#e0e5e0] bg-white shadow-sm">
          <div className="flex flex-col gap-3 border-b border-[#edf0ed] p-4 sm:flex-row sm:items-center sm:justify-between sm:px-6">
            <div>
              <h2 className="font-bold">Nhân viên ({filteredUsers.length})</h2>
              <p className="mt-1 text-xs text-[#818a83]">Quyền được gán theo từng membership/ngữ cảnh.</p>
            </div>
            <input
              aria-label="Tìm tài khoản"
              className="h-10 rounded-lg border border-[#d8ded8] px-3 text-sm outline-none focus:border-[#28734d]"
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Tìm tên, email hoặc mã..."
              value={search}
            />
          </div>
          {loading ? (
            <p className="p-8 text-center text-sm text-[#7c857e]">Đang tải danh sách...</p>
          ) : filteredUsers.length === 0 ? (
            <p className="p-8 text-center text-sm text-[#7c857e]">Không tìm thấy tài khoản.</p>
          ) : (
            <div className="divide-y divide-[#edf0ed]">
              {filteredUsers.map((item) => (
                <UserRow
                  directory={directory}
                  key={item.user_code}
                  onAddMembership={() => {
                    setMembershipFor(item.user_code);
                    setNewMembership({
                      ...emptyMembership,
                      unit_code: directory.units[0]?.unit_code ?? "",
                      role_code: directory.roles.find((role) => role.role_code === "employee")?.role_code ?? "",
                    });
                  }}
                  onChangeRole={(membershipId, roleCode) =>
                    void runAction(
                      () => changeAdminMembershipRole(user.id, membershipId, roleCode),
                      "Đã cập nhật vai trò.",
                    )
                  }
                  onRevoke={(membership) => {
                    if (window.confirm(`Thu hồi membership ${membership.context_id}?`)) {
                      void runAction(
                        () => revokeAdminMembership(user.id, membership.membership_id),
                        "Đã thu hồi membership.",
                      );
                    }
                  }}
                  onToggleStatus={(status) =>
                    void runAction(
                      () => setAdminUserStatus(user.id, item.user_code, status),
                      status === "disabled" ? "Đã đóng tài khoản." : "Đã mở lại tài khoản.",
                    )
                  }
                  saving={saving}
                  user={item}
                >
                  {membershipFor === item.user_code && (
                    <form className="mt-4 grid gap-3 rounded-xl border border-[#dfe8e1] bg-white p-4 md:grid-cols-6" onSubmit={(event) => void handleAddMembership(event, item)}>
                      <label className="text-xs font-semibold text-[#556058] md:col-span-2">Ngữ cảnh
                        <input className={inputClass} maxLength={150} onChange={(event) => setNewMembership({ ...newMembership, context_id: event.target.value })} required value={newMembership.context_id} />
                      </label>
                      <label className="text-xs font-semibold text-[#556058]">Đơn vị
                        <select className={inputClass} onChange={(event) => setNewMembership({ ...newMembership, unit_code: event.target.value })} value={newMembership.unit_code}>
                          {directory.units.map((unit) => <option key={unit.unit_code} value={unit.unit_code}>{unit.name}</option>)}
                        </select>
                      </label>
                      <label className="text-xs font-semibold text-[#556058]">Vai trò
                        <select className={inputClass} onChange={(event) => setNewMembership({ ...newMembership, role_code: event.target.value })} value={newMembership.role_code}>
                          {directory.roles.map((role) => <option key={role.role_code} value={role.role_code}>{role.role_name}</option>)}
                        </select>
                      </label>
                      <label className="text-xs font-semibold text-[#556058]">Loại membership
                        <select className={inputClass} onChange={(event) => setNewMembership({ ...newMembership, membership_type: event.target.value as MembershipForm["membership_type"] })} value={newMembership.membership_type}>
                          <option value="primary">Chính</option><option value="matrix">Kiêm nhiệm</option><option value="project">Dự án</option>
                        </select>
                      </label>
                      <div className="flex items-end gap-2">
                        <button className="h-10 rounded-lg bg-[#165c3a] px-3 text-xs font-bold text-white disabled:opacity-60" disabled={saving} type="submit">Cấp quyền</button>
                        <button className="h-10 rounded-lg px-3 text-xs font-bold text-[#667168] hover:bg-[#edf1ed]" onClick={() => setMembershipFor(null)} type="button">Hủy</button>
                      </div>
                      <label className="text-xs font-semibold text-[#556058] md:col-span-3">Chức danh
                        <input className={inputClass} maxLength={150} onChange={(event) => setNewMembership({ ...newMembership, job_title: event.target.value })} value={newMembership.job_title} />
                      </label>
                    </form>
                  )}
                </UserRow>
              ))}
            </div>
          )}
        </section>
      </main>

      {createOpen && (
        <div className="fixed inset-0 z-20 flex items-center justify-center overflow-y-auto bg-[#172019]/40 p-4">
          <form className="my-auto w-full max-w-3xl rounded-2xl bg-white p-6 shadow-xl" onSubmit={(event) => void handleCreateUser(event)}>
            <div className="flex items-start justify-between">
              <div><h2 className="text-xl font-bold">Tạo tài khoản nhân viên</h2><p className="mt-1 text-sm text-[#788179]">Tài khoản được tạo active, kèm membership và role ban đầu.</p></div>
              <button aria-label="Đóng" className="rounded-lg px-3 py-1 text-xl text-[#68736a] hover:bg-[#f1f3f0]" onClick={() => setCreateOpen(false)} type="button">×</button>
            </div>
            <div className="mt-5 grid gap-4 sm:grid-cols-2">
              <label className="text-xs font-semibold text-[#556058]">Mã tài khoản<input className={inputClass} maxLength={100} onChange={(event) => setNewUser({ ...newUser, user_code: event.target.value })} required value={newUser.user_code} /></label>
              <label className="text-xs font-semibold text-[#556058]">Họ tên<input className={inputClass} maxLength={150} onChange={(event) => setNewUser({ ...newUser, display_name: event.target.value })} required value={newUser.display_name} /></label>
              <label className="text-xs font-semibold text-[#556058]">Email<input className={inputClass} onChange={(event) => setNewUser({ ...newUser, email: event.target.value })} required type="email" value={newUser.email} /></label>
              <label className="text-xs font-semibold text-[#556058]">Mật khẩu ban đầu<input className={inputClass} minLength={12} onChange={(event) => setNewUser({ ...newUser, password: event.target.value })} required type="password" value={newUser.password} /><span className="mt-1 block font-normal text-[#818a83]">Tối thiểu 12 ký tự.</span></label>
              <label className="text-xs font-semibold text-[#556058]">Ngữ cảnh / context ID<input className={inputClass} maxLength={150} onChange={(event) => setNewUser({ ...newUser, context_id: event.target.value })} required value={newUser.context_id} /></label>
              <label className="text-xs font-semibold text-[#556058]">Chức danh<input className={inputClass} maxLength={150} onChange={(event) => setNewUser({ ...newUser, job_title: event.target.value })} value={newUser.job_title} /></label>
              <label className="text-xs font-semibold text-[#556058]">Đơn vị<select className={inputClass} onChange={(event) => setNewUser({ ...newUser, unit_code: event.target.value })} required value={newUser.unit_code}>{directory.units.map((unit) => <option key={unit.unit_code} value={unit.unit_code}>{unit.name} · {unit.unit_code}</option>)}</select></label>
              <label className="text-xs font-semibold text-[#556058]">Role<select className={inputClass} onChange={(event) => setNewUser({ ...newUser, role_code: event.target.value })} required value={newUser.role_code}>{directory.roles.map((role) => <option key={role.role_code} value={role.role_code}>{role.role_name} · {role.role_code}</option>)}</select></label>
              <label className="text-xs font-semibold text-[#556058]">Loại membership<select className={inputClass} onChange={(event) => setNewUser({ ...newUser, membership_type: event.target.value as MembershipForm["membership_type"] })} value={newUser.membership_type}><option value="primary">Chính</option><option value="matrix">Kiêm nhiệm</option><option value="project">Dự án</option></select></label>
            </div>
            <div className="mt-6 flex justify-end gap-2">
              <button className="h-10 rounded-lg px-4 text-sm font-semibold text-[#667168] hover:bg-[#f1f3f0]" onClick={() => setCreateOpen(false)} type="button">Hủy</button>
              <button className="h-10 rounded-lg bg-[#165c3a] px-5 text-sm font-bold text-white disabled:opacity-60" disabled={saving || !directory.units.length || !directory.roles.length} type="submit">{saving ? "Đang lưu..." : "Tạo tài khoản"}</button>
            </div>
          </form>
        </div>
      )}

      {unitDialog && (
        <div className="fixed inset-0 z-30 flex items-center justify-center overflow-y-auto bg-[#172019]/40 p-4">
          <form
            className="my-auto w-full max-w-xl rounded-2xl bg-white p-6 shadow-xl"
            onSubmit={(event) => void handleSaveUnit(event)}
          >
            <div className="flex items-start justify-between">
              <div>
                <h2 className="text-xl font-bold">
                  {unitDialog === "create" ? "Tạo organization unit" : "Chỉnh sửa organization unit"}
                </h2>
                <p className="mt-1 text-sm text-[#788179]">
                  Đơn vị cha phải đúng cấp; không thể ngừng đơn vị còn đơn vị con hoặc membership active.
                </p>
              </div>
              <button
                aria-label="Đóng"
                className="rounded-lg px-3 py-1 text-xl text-[#68736a] hover:bg-[#f1f3f0]"
                onClick={() => setUnitDialog(null)}
                type="button"
              >
                ×
              </button>
            </div>
            <div className="mt-5 grid gap-4 sm:grid-cols-2">
              {unitDialog === "create" ? (
                <>
                  <label className="text-xs font-semibold text-[#556058]">
                    Mã đơn vị
                    <input
                      className={inputClass}
                      maxLength={50}
                      onChange={(event) => setUnitForm({ ...unitForm, unit_code: event.target.value })}
                      pattern="[a-zA-Z0-9][a-zA-Z0-9_.-]*"
                      required
                      value={unitForm.unit_code}
                    />
                  </label>
                  <label className="text-xs font-semibold text-[#556058]">
                    Loại đơn vị
                    <select
                      className={inputClass}
                      onChange={(event) => {
                        const unitType = event.target.value as AdminOrganizationUnitType;
                        const firstParent = organizationUnits.find(
                          (unit) =>
                            unit.status === "active" &&
                            allowedParentTypes[unitType].includes(unit.unit_type),
                        );
                        setUnitForm({
                          ...unitForm,
                          unit_type: unitType,
                          parent_unit_code: firstParent?.unit_code ?? "",
                        });
                      }}
                      value={unitForm.unit_type}
                    >
                      {Object.entries(unitTypeLabels).map(([value, label]) => (
                        <option key={value} value={value}>{label}</option>
                      ))}
                    </select>
                  </label>
                </>
              ) : (
                <div className="text-xs font-semibold text-[#556058]">
                  Mã / loại đơn vị
                  <p className="mt-2 text-sm font-normal text-[#68736a]">
                    {unitForm.unit_code} · {unitTypeLabels[unitForm.unit_type]}
                  </p>
                </div>
              )}
              <label className="text-xs font-semibold text-[#556058] sm:col-span-2">
                Tên đơn vị
                <input
                  className={inputClass}
                  maxLength={150}
                  onChange={(event) => setUnitForm({ ...unitForm, name: event.target.value })}
                  required
                  value={unitForm.name}
                />
              </label>
              <label className="text-xs font-semibold text-[#556058] sm:col-span-2">
                Đơn vị cha
                <select
                  className={inputClass}
                  onChange={(event) => setUnitForm({ ...unitForm, parent_unit_code: event.target.value })}
                  required={unitForm.unit_type !== "group"}
                  value={unitForm.parent_unit_code}
                >
                  {unitForm.unit_type === "group" && <option value="">Không có (đơn vị gốc)</option>}
                  {selectableParents.map((unit) => (
                    <option key={unit.unit_code} value={unit.unit_code}>
                      {unit.name} · {unitTypeLabels[unit.unit_type]}
                    </option>
                  ))}
                  {unitDialog !== "create" &&
                    unitForm.parent_unit_code &&
                    !selectableParents.some((unit) => unit.unit_code === unitForm.parent_unit_code) && (
                      <option value={unitForm.parent_unit_code}>
                        {unitDialog.parent_name ?? unitForm.parent_unit_code} · parent hiện tại
                      </option>
                    )}
                </select>
              </label>
              {unitDialog !== "create" && (
                <label className="text-xs font-semibold text-[#556058] sm:col-span-2">
                  Trạng thái
                  <select
                    className={inputClass}
                    onChange={(event) =>
                      setUnitForm({
                        ...unitForm,
                        status: event.target.value as OrganizationUnitForm["status"],
                      })
                    }
                    value={unitForm.status}
                  >
                    <option value="active">Đang hoạt động</option>
                    <option value="inactive">Ngừng hoạt động</option>
                  </select>
                </label>
              )}
            </div>
            <div className="mt-6 flex justify-end gap-2">
              <button
                className="h-10 rounded-lg px-4 text-sm font-semibold text-[#667168] hover:bg-[#f1f3f0]"
                onClick={() => setUnitDialog(null)}
                type="button"
              >
                Hủy
              </button>
              <button
                className="h-10 rounded-lg bg-[#165c3a] px-5 text-sm font-bold text-white disabled:opacity-60"
                disabled={saving}
                type="submit"
              >
                {saving ? "Đang lưu..." : "Lưu"}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}

function UserRow({
  user,
  directory,
  saving,
  onToggleStatus,
  onAddMembership,
  onChangeRole,
  onRevoke,
  children,
}: {
  user: AdminUser;
  directory: AdminDirectory;
  saving: boolean;
  onToggleStatus: (status: "active" | "disabled") => void;
  onAddMembership: () => void;
  onChangeRole: (membershipId: string, roleCode: string) => void;
  onRevoke: (membership: AdminMembership) => void;
  children: React.ReactNode;
}) {
  return (
    <article className="p-4 sm:px-6">
      <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-center">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="font-bold">{user.display_name}</h3>
            <span className={`rounded-full px-2.5 py-1 text-[10px] font-bold ${user.status === "active" ? "bg-[#eaf5ed] text-[#276342]" : "bg-[#f3eeee] text-[#8b4540]"}`}>
              {user.status === "active" ? "Đang hoạt động" : "Đã khóa"}
            </span>
            <span className="text-xs text-[#879087]">{user.user_code}</span>
          </div>
          <p className="mt-1 text-sm text-[#6f7971]">{user.email}</p>
        </div>
        <div className="flex shrink-0 gap-2">
          <button className="rounded-lg border border-[#dce3dd] px-3 py-2 text-xs font-bold text-[#356048] hover:bg-[#f4f8f5]" onClick={onAddMembership} type="button">+ Cấp quyền / đơn vị</button>
          <button className={`rounded-lg px-3 py-2 text-xs font-bold disabled:opacity-50 ${user.status === "active" ? "border border-[#edd5d1] text-[#a1453b] hover:bg-[#fff3f0]" : "bg-[#e8f3ed] text-[#276342] hover:bg-[#d9ecdf]"}`} disabled={saving} onClick={() => onToggleStatus(user.status === "active" ? "disabled" : "active")} type="button">
            {user.status === "active" ? "Khóa tài khoản" : "Mở tài khoản"}
          </button>
        </div>
      </div>
      <div className="mt-4 space-y-2">
        {user.memberships.map((membership) => {
          const active = membership.status === "active";
          const activeRole = membership.roles.find((role) => role.status === "active");
          return (
            <div className={`grid gap-3 rounded-xl px-3 py-3 md:grid-cols-[minmax(0,1.3fr)_minmax(150px,1fr)_minmax(150px,1fr)_auto] md:items-center ${active ? "bg-[#f6f8f6]" : "bg-[#f6f3f3] opacity-70"}`} key={membership.membership_id}>
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold">{membership.unit_name} <span className="font-normal text-[#7c867e]">· {membership.context_id}</span></p>
                <p className="mt-1 text-[11px] text-[#818a83]">{membership.job_title || "Chưa có chức danh"} · {membership.membership_type}</p>
              </div>
              {active ? (
                <label className="text-[10px] font-bold uppercase tracking-wide text-[#859087]">Vai trò
                  <select className="mt-1 h-9 w-full rounded-lg border border-[#dce2dc] bg-white px-2 text-xs font-semibold text-[#39443c]" disabled={saving} onChange={(event) => onChangeRole(membership.membership_id, event.target.value)} value={activeRole?.role_code ?? ""}>
                    {!activeRole && <option value="">Chọn vai trò</option>}
                    {directory.roles.map((role) => <option key={role.role_code} value={role.role_code}>{role.role_name}</option>)}
                  </select>
                </label>
              ) : (
                <p className="text-xs font-semibold text-[#8c7774]">Đã thu hồi</p>
              )}
              <div className="text-xs text-[#6f7971]">{membership.roles.map((role) => role.role_name).join(", ") || "Chưa gán role"}</div>
              {active && <button className="justify-self-start rounded-lg px-2 py-2 text-xs font-bold text-[#a1453b] hover:bg-white md:justify-self-end" disabled={saving} onClick={() => onRevoke(membership)} type="button">Thu hồi</button>}
            </div>
          );
        })}
        {!user.memberships.length && <p className="text-xs text-[#879087]">Tài khoản chưa có membership.</p>}
      </div>
      {children}
    </article>
  );
}

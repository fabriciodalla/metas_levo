import { Trash2 } from "lucide-react";
import { useState, type FormEvent } from "react";
import { api, ApiError } from "../../api/client";
import type { HierarchyNode, RepresentanteInput, UserAccount, UserAccountInput } from "../../api/types";
import { Button } from "../../components/ui/Button";
import { Alert } from "../../components/ui/Alert";
import { Modal } from "../../components/ui/Modal";
import { LEVELS, LEVEL_LABELS, type Level } from "./constants";

export type UserEditModalTarget =
  | { kind: "user"; user: UserAccount | null }
  | { kind: "representante"; node: HierarchyNode | null };

// A pessoa tem nome de verdade na hierarquia (ex.: "Fabio Shaen") — mostra isso em vez do login
// técnico ("fabio.shaen") sempre que der, pra não duplicar a mesma pessoa com dois rótulos.
export function displayName(user: UserAccount): string {
  return user.hierarchy_nodes[0]?.nome ?? user.username;
}

function formFromUser(user: UserAccount | null): UserAccountInput {
  const node = user?.hierarchy_nodes[0];
  return {
    username: user?.username ?? "",
    email: user?.email ?? "",
    password: "",
    is_admin: user?.is_admin ?? false,
    is_active: user?.is_active ?? true,
    level: node?.level ?? null,
    parent_node_id: node?.parent ?? null,
  };
}

function formFromRepresentante(node: HierarchyNode | null): UserAccountInput {
  return {
    username: node?.nome ?? "",
    email: "",
    password: "",
    is_admin: false,
    is_active: node?.ativo ?? true,
    level: "VENDEDOR",
    parent_node_id: node?.parent ?? null,
  };
}

function AddPositionRow({
  nodes,
  onAdd,
}: {
  nodes: HierarchyNode[];
  onAdd: (level: Level, parentNodeId: number | null) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [level, setLevel] = useState<Level | "">("");
  const [parentNodeId, setParentNodeId] = useState<number | "">("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const levelIndex = level ? LEVELS.indexOf(level) : -1;
  const superiorOptions =
    levelIndex > 0 ? nodes.filter((n) => n.level === LEVELS[levelIndex - 1] && n.ativo) : [];

  function reset() {
    setOpen(false);
    setLevel("");
    setParentNodeId("");
    setError(null);
  }

  async function handleAdd() {
    if (!level) return;
    if (levelIndex > 0 && !parentNodeId) {
      setError("Escolha o superior dessa posição.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await onAdd(level, parentNodeId ? Number(parentNodeId) : null);
      reset();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Falha ao adicionar cargo.");
    } finally {
      setSaving(false);
    }
  }

  if (!open) {
    return (
      <Button type="button" variant="ghost" size="sm" onClick={() => setOpen(true)}>
        + Adicionar outro cargo
      </Button>
    );
  }

  return (
    <div className="mt-2">
      <div className="field-group">
        <div className="field">
          <label className="field-label" htmlFor="new-position-level">
            Cargo adicional
          </label>
          <select
            id="new-position-level"
            value={level}
            onChange={(e) => {
              setLevel(e.target.value as Level | "");
              setParentNodeId("");
            }}
          >
            <option value="">Selecione…</option>
            {LEVELS.map((lvl) => (
              <option key={lvl} value={lvl}>
                {LEVEL_LABELS[lvl]}
              </option>
            ))}
          </select>
        </div>
        {levelIndex > 0 && (
          <div className="field">
            <label className="field-label" htmlFor="new-position-superior">
              Superior ({LEVEL_LABELS[LEVELS[levelIndex - 1]]})
            </label>
            <select
              id="new-position-superior"
              value={parentNodeId}
              onChange={(e) => setParentNodeId(e.target.value ? Number(e.target.value) : "")}
            >
              <option value="" disabled>
                Selecione…
              </option>
              {superiorOptions.map((n) => (
                <option key={n.id} value={n.id}>
                  {n.nome}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>
      {error && (
        <Alert variant="danger" role="alert">
          {error}
        </Alert>
      )}
      <div className="field-group mt-2">
        <Button type="button" size="sm" onClick={() => void handleAdd()} disabled={saving || !level}>
          {saving ? "Adicionando…" : "Adicionar"}
        </Button>
        <Button type="button" variant="ghost" size="sm" onClick={reset}>
          Cancelar
        </Button>
      </div>
    </div>
  );
}

export function UserEditModal({
  target,
  nodes,
  onClose,
  onSaved,
  onChanged,
}: {
  target: UserEditModalTarget;
  nodes: HierarchyNode[];
  onClose: () => void;
  onSaved: () => void;
  onChanged?: () => void;
}) {
  const user = target.kind === "user" ? target.user : null;
  const representanteNode = target.kind === "representante" ? target.node : null;
  const editingId = target.kind === "user" ? target.user?.id ?? null : target.node?.id ?? null;
  const isNew = editingId === null;

  const [form, setForm] = useState<UserAccountInput>(() =>
    target.kind === "representante" ? formFromRepresentante(target.node) : formFromUser(target.user)
  );
  // Representante: Vendedor sem usuário vinculado por design, sem e-mail/senha (ver
  // HierarchyNode.is_representante). Só escolhível ao criar — quem já existe como usuário ou já
  // existe como representante não muda de tipo depois.
  const [isRepresentante, setIsRepresentante] = useState(target.kind === "representante");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  // Posições além da principal (a primeira, editada pelos campos Cargo/Superior acima) — mesma
  // pessoa pode acumular mais de um cargo na árvore (Decisão 10/O5 revisada, 2026-07-21).
  const [extraPositions, setExtraPositions] = useState<HierarchyNode[]>(user?.hierarchy_nodes.slice(1) ?? []);
  const [positionError, setPositionError] = useState<string | null>(null);

  // Cargo + Superior bastam pra identificar a posição — o backend resolve o nó (reaproveita um
  // já cadastrado sem usuário com esse cargo/superior/nome, ou cria um novo). Nenhum seletor
  // manual de nó aqui (Decisão 11 revisada, 2026-07-21).
  const level = (form.level ?? "") as Level | "";
  const levelIndex = level ? LEVELS.indexOf(level) : -1;
  const superiorOptions =
    levelIndex > 0 ? nodes.filter((n) => n.level === LEVELS[levelIndex - 1] && n.ativo) : [];

  function toggleRepresentante(checked: boolean) {
    setIsRepresentante(checked);
    setForm((f) => ({
      ...f,
      level: checked ? "VENDEDOR" : null,
      parent_node_id: null,
      email: checked ? "" : f.email,
      password: checked ? "" : f.password,
      is_admin: checked ? false : f.is_admin,
    }));
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      if (isRepresentante) {
        const payload: RepresentanteInput = {
          nome: form.username,
          parent: form.parent_node_id ?? null,
          ativo: form.is_active ?? true,
        };
        if (editingId === null) {
          await api.post("/hierarchy/nodes/", { ...payload, level: "VENDEDOR", is_representante: true });
        } else {
          await api.patch(`/hierarchy/nodes/${editingId}/`, payload);
        }
      } else {
        const payload: UserAccountInput = { ...form };
        if (editingId !== null && !payload.password) {
          delete payload.password;
        }
        if (editingId === null) {
          await api.post("/accounts/users/", payload);
        } else {
          await api.patch(`/accounts/users/${editingId}/`, payload);
        }
      }
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Falha ao salvar.");
    } finally {
      setSaving(false);
    }
  }

  async function handleAddPosition(newLevel: Level, parentNodeId: number | null) {
    if (editingId === null) return;
    const updated = await api.post<UserAccount>(`/accounts/users/${editingId}/positions/`, {
      level: newLevel,
      parent_node_id: parentNodeId,
    });
    setExtraPositions(updated.hierarchy_nodes.slice(1));
    setPositionError(null);
    onChanged?.();
  }

  async function handleRemovePosition(nodeId: number) {
    if (editingId === null) return;
    setPositionError(null);
    try {
      await api.delete(`/accounts/users/${editingId}/positions/${nodeId}/`);
      setExtraPositions((prev) => prev.filter((n) => n.id !== nodeId));
      onChanged?.();
    } catch (err) {
      setPositionError(err instanceof ApiError ? err.message : "Falha ao remover cargo.");
    }
  }

  const currentName =
    target.kind === "representante"
      ? representanteNode?.nome ?? form.username
      : user
        ? displayName(user)
        : form.username;

  return (
    <Modal
      title={
        isNew
          ? isRepresentante
            ? "Novo representante"
            : "Novo usuário"
          : `Editando: ${currentName}`
      }
      onClose={onClose}
      size="lg"
    >
      <form onSubmit={(e) => void handleSubmit(e)}>
        {isNew && (
          <label className="field-check">
            <input
              type="checkbox"
              checked={isRepresentante}
              onChange={(e) => toggleRepresentante(e.target.checked)}
            />
            Representante (sem acesso ao sistema)
          </label>
        )}
        {(isNew || isRepresentante) && (
          <p className="field-hint mt-0">
            Representante entra na hierarquia normalmente (acumulado e distribuição de meta como
            Vendedor), mas não tem e-mail nem senha — não loga no sistema.
          </p>
        )}
        <div className="field-group">
          <div className="field">
            <label className="field-label" htmlFor="user-username">
              Nome completo
            </label>
            <input
              id="user-username"
              value={form.username}
              onChange={(e) => setForm({ ...form, username: e.target.value })}
              required
              autoFocus
            />
          </div>
          {!isRepresentante && (
            <>
              <div className="field">
                <label className="field-label" htmlFor="user-email">
                  E-mail
                </label>
                <input
                  id="user-email"
                  type="email"
                  value={form.email}
                  onChange={(e) => setForm({ ...form, email: e.target.value })}
                  required
                />
              </div>
              <div className="field">
                <label className="field-label" htmlFor="user-password">
                  Senha{editingId !== null ? " (deixe em branco para manter a atual)" : ""}
                </label>
                <input
                  id="user-password"
                  type="password"
                  value={form.password ?? ""}
                  onChange={(e) => setForm({ ...form, password: e.target.value })}
                  required={editingId === null}
                />
              </div>
            </>
          )}
        </div>
        {!isRepresentante && (
          <label className="field-check">
            <input
              type="checkbox"
              checked={form.is_admin ?? false}
              onChange={(e) => setForm({ ...form, is_admin: e.target.checked })}
            />
            Administrador
          </label>
        )}
        <label className="field-check">
          <input
            type="checkbox"
            checked={form.is_active ?? true}
            onChange={(e) => setForm({ ...form, is_active: e.target.checked })}
          />
          Ativo
        </label>

        <div className="field-group mb-3">
          <div className="field">
            <label className="field-label" htmlFor="user-level">
              Cargo
            </label>
            <select
              id="user-level"
              value={level}
              disabled={isRepresentante}
              onChange={(e) => {
                const value = e.target.value as Level | "";
                setForm({ ...form, level: value || null, parent_node_id: null });
              }}
            >
              {isRepresentante ? (
                <option value="VENDEDOR">Vendedor</option>
              ) : (
                <>
                  <option value="">Sem cargo (só Administrador)</option>
                  {LEVELS.map((lvl) => (
                    <option key={lvl} value={lvl}>
                      {LEVEL_LABELS[lvl]}
                    </option>
                  ))}
                </>
              )}
            </select>
          </div>
          {levelIndex > 0 && (
            <div className="field">
              <label className="field-label" htmlFor="user-superior">
                Superior ({LEVEL_LABELS[LEVELS[levelIndex - 1]]})
              </label>
              <select
                id="user-superior"
                value={form.parent_node_id ?? ""}
                onChange={(e) =>
                  setForm({ ...form, parent_node_id: e.target.value ? Number(e.target.value) : null })
                }
                required
              >
                <option value="" disabled>
                  Selecione…
                </option>
                {superiorOptions.map((n) => (
                  <option key={n.id} value={n.id}>
                    {n.nome}
                  </option>
                ))}
              </select>
            </div>
          )}
        </div>

        {error && (
          <Alert variant="danger" role="alert">
            {error}
          </Alert>
        )}
        <div className="field-group mt-4">
          <Button type="submit" disabled={saving}>
            {saving ? "Salvando…" : editingId === null ? "Criar" : "Salvar"}
          </Button>
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancelar
          </Button>
        </div>
      </form>

      {editingId !== null && !isRepresentante && (
        <div className="modal-section mt-5 pt-4">
          <label className="field-label">Outros cargos</label>
          <p className="field-hint mt-0">
            A mesma pessoa pode ocupar mais de uma posição na árvore (ex.: também ser Coordenador
            Local de um dos ramos que reportam a ela).
          </p>

          {extraPositions.length > 0 && (
            <ul className="position-list">
              {extraPositions.map((node) => (
                <li key={node.id} className="position-list-item">
                  <span>
                    {node.level_display} — {node.nome}
                  </span>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="btn-icon"
                    onClick={() => void handleRemovePosition(node.id)}
                    aria-label={`Remover cargo ${node.level_display}`}
                  >
                    <Trash2 size={14} />
                  </Button>
                </li>
              ))}
            </ul>
          )}

          {positionError && (
            <Alert variant="danger" role="alert">
              {positionError}
            </Alert>
          )}

          <AddPositionRow nodes={nodes} onAdd={handleAddPosition} />
        </div>
      )}
    </Modal>
  );
}

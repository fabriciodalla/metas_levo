import { ChevronDown, ChevronRight, Pencil, TriangleAlert, User } from "lucide-react";
import { useEffect, useState } from "react";
import { api, ApiError } from "../../api/client";
import type { HierarchyNode, UserAccount } from "../../api/types";
import { Card } from "../../components/ui/Card";
import { Alert } from "../../components/ui/Alert";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { Modal } from "../../components/ui/Modal";
import { UserEditModal } from "./UserEditModal";

function TreeNode({
  node,
  nodes,
  depth,
  expanded,
  onToggle,
  onEdit,
  ancestorIds,
}: {
  node: HierarchyNode;
  nodes: HierarchyNode[];
  depth: number;
  expanded: Set<number>;
  onToggle: (id: number) => void;
  onEdit: (node: HierarchyNode) => void;
  ancestorIds: Set<number>;
}) {
  // Dado corrompido (ex.: nó editado no Django Admin apontando pra si mesmo como pai) não pode
  // travar a árvore inteira num loop infinito de render — um nó já visto na cadeia de
  // ancestrais nunca é desenhado de novo como seu próprio filho/descendente.
  const selfAndAncestors = new Set(ancestorIds).add(node.id);
  const children = nodes.filter((n) => n.parent === node.id && !selfAndAncestors.has(n.id));
  const hasChildren = children.length > 0;
  const isOpen = expanded.has(node.id);

  return (
    <li>
      <div
        className={["tree-node-row", !node.ativo ? "tree-node-inactive" : ""].filter(Boolean).join(" ")}
        style={{ paddingLeft: depth * 20 }}
      >
        <button
          type="button"
          className="tree-toggle"
          onClick={() => onToggle(node.id)}
          disabled={!hasChildren}
          aria-label={isOpen ? `Recolher ${node.nome}` : `Expandir ${node.nome}`}
        >
          {hasChildren && (isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />)}
        </button>
        <span className="tree-node-avatar">
          <User size={14} />
        </span>
        <span className="tree-node-name">{node.nome}</span>
        <span className="tree-node-level">{node.level_display}</span>
        {!node.ativo && <Badge variant="neutral">inativo</Badge>}
        {hasChildren && <span className="tree-node-count">{children.length}</span>}
        <button
          type="button"
          className="tree-node-edit"
          onClick={() => onEdit(node)}
          aria-label={`Editar ${node.nome}`}
        >
          <Pencil size={14} />
        </button>
      </div>
      {isOpen && hasChildren && (
        <ul className="tree-children">
          {children.map((child) => (
            <TreeNode
              key={child.id}
              node={child}
              nodes={nodes}
              depth={depth + 1}
              expanded={expanded}
              onToggle={onToggle}
              onEdit={onEdit}
              ancestorIds={selfAndAncestors}
            />
          ))}
        </ul>
      )}
    </li>
  );
}

// Dado corrompido (ex.: um nó editado no Django Admin apontando pra si mesmo como pai — o caso
// real que motivou isso, já que o seletor de pai lá é um FK cru sem essa checagem) nunca chega a
// um Gerente de verdade subindo a cadeia de pais — a árvore, desenhada de cima pra baixo a partir
// das raízes, jamais alcançaria um nó assim, e ele ficaria escondido pra sempre sem nenhum jeito
// de clicar nele pela UI. Só o nó que aponta pra si mesmo vira raiz extra — os filhos dele (reais,
// não corrompidos) continuam pendurados nele normalmente, sem contar de novo aqui, senão
// apareceriam duplicados (uma vez aninhados, outra vez soltos no topo).
function findSelfParentedIds(allNodes: HierarchyNode[]): Set<number> {
  return new Set(allNodes.filter((n) => n.parent === n.id).map((n) => n.id));
}

export function HierarchyManager() {
  const [nodes, setNodes] = useState<HierarchyNode[]>([]);
  const [users, setUsers] = useState<UserAccount[]>([]);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [modalUser, setModalUser] = useState<UserAccount | null>(null);
  const [orphanNode, setOrphanNode] = useState<HierarchyNode | null>(null);
  const [deactivating, setDeactivating] = useState(false);
  const [deactivateError, setDeactivateError] = useState<string | null>(null);
  const [showInactive, setShowInactive] = useState(false);

  function reload() {
    void api.get<HierarchyNode[]>("/hierarchy/nodes/").then(setNodes);
    void api.get<UserAccount[]>("/accounts/users/").then(setUsers);
  }

  useEffect(reload, []);

  function toggleExpand(nodeId: number) {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(nodeId)) {
        next.delete(nodeId);
      } else {
        next.add(nodeId);
      }
      return next;
    });
  }

  function startEdit(node: HierarchyNode) {
    const occupant = users.find((u) => u.hierarchy_nodes.some((n) => n.id === node.id));
    if (!occupant) {
      // Nó sem usuário vinculado (ex.: sobra de uma reorganização) não tem o que editar —
      // só oferece desativar, em vez de travar sem nenhuma ação possível.
      setDeactivateError(null);
      setOrphanNode(node);
      return;
    }
    setOrphanNode(null);
    setModalUser(occupant);
  }

  function handleSaved() {
    reload();
    setModalUser(null);
  }

  async function confirmDeactivate() {
    if (!orphanNode) return;
    setDeactivating(true);
    setDeactivateError(null);
    try {
      await api.patch(`/hierarchy/nodes/${orphanNode.id}/`, { ativo: false });
      setOrphanNode(null);
      reload();
    } catch (err) {
      setDeactivateError(err instanceof ApiError ? err.message : "Falha ao desativar o nó.");
    } finally {
      setDeactivating(false);
    }
  }

  // Nó inativo (Decisão 8/10) some da árvore por padrão — "Mostrar inativos" revela de volta,
  // pra dar pra checar histórico sem ficar poluindo a visão do dia a dia.
  const visibleNodes = showInactive ? nodes : nodes.filter((n) => n.ativo);
  const selfParentedIds = findSelfParentedIds(nodes);
  const roots = visibleNodes.filter((n) => n.parent === null || selfParentedIds.has(n.id));

  return (
    <div>
      <Card
        title="Hierarquia"
        actions={
          <label className="field-check mb-0">
            <input
              type="checkbox"
              checked={showInactive}
              onChange={(e) => setShowInactive(e.target.checked)}
            />
            Mostrar inativos
          </label>
        }
      >
        <div className="tree-scroll">
          {roots.length === 0 ? (
            <EmptyState>Nenhum nó cadastrado ainda.</EmptyState>
          ) : (
            <ul className="tree-root">
              {roots.map((root) => (
                <TreeNode
                  key={root.id}
                  node={root}
                  nodes={visibleNodes}
                  depth={0}
                  expanded={expanded}
                  onToggle={toggleExpand}
                  onEdit={startEdit}
                  ancestorIds={new Set()}
                />
              ))}
            </ul>
          )}
        </div>
      </Card>

      {modalUser && (
        <UserEditModal
          target={{ kind: "user", user: modalUser }}
          nodes={nodes}
          onClose={() => setModalUser(null)}
          onSaved={handleSaved}
          onChanged={reload}
        />
      )}

      {orphanNode && orphanNode.is_representante && (
        <Modal title="Representante" onClose={() => setOrphanNode(null)}>
          <p>
            <strong>{orphanNode.nome}</strong> ({orphanNode.level_display}) é um representante —
            participa do acumulado e da distribuição de meta, mas não tem usuário nem login. Pra
            editar nome, superior ou desativar, use a tela Gestão → Usuários.
          </p>
          <div className="field-group mt-4">
            <Button type="button" variant="secondary" onClick={() => setOrphanNode(null)}>
              Fechar
            </Button>
          </div>
        </Modal>
      )}

      {orphanNode && !orphanNode.is_representante && (
        <Modal title="Nó sem usuário vinculado" onClose={() => setOrphanNode(null)}>
          <p>
            <strong>{orphanNode.nome}</strong> ({orphanNode.level_display}) não tem nenhum usuário
            vinculado, então não há o que editar aqui. Se esse cargo não existe mais (ex.: substituído
            por outra posição), você pode desativá-lo — ele some da árvore ativa, mas o histórico é
            preservado.
          </p>
          {deactivateError && (
            <Alert variant="danger" role="alert">
              <TriangleAlert size={14} /> {deactivateError}
            </Alert>
          )}
          <div className="field-group mt-4">
            <Button variant="danger" onClick={() => void confirmDeactivate()} disabled={deactivating}>
              {deactivating ? "Desativando…" : "Desativar"}
            </Button>
            <Button type="button" variant="secondary" onClick={() => setOrphanNode(null)}>
              Cancelar
            </Button>
          </div>
        </Modal>
      )}
    </div>
  );
}

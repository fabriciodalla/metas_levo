import type { HierarchyNode } from "../../api/types";

// Janela simples com os nomes dos filhos diretos da própria posição de login do usuário (sempre
// um nível abaixo — mesma regra da tabela "Equipe" do Acumulado de Vendas,
// `_effective_team_children` em `apps/allocations/results.py`), pra trocar de nó sem precisar
// voltar pra hierarquia. Sem filhos ativos (ex.: usuário logado como Vendedor, fim da árvore), o
// campo nem aparece — ver AcumuladoClientesPage/useAcumuladoClientes.
//
// Controlado por `selectedNodeId`, não por estado próprio (revisão 2026-09-18, terceira volta): a
// lista é sempre a mesma (os irmãos, fixos em `ownNodeId` — ver useAcumuladoClientes), então o
// <select> precisa refletir QUAL desses irmãos está sendo visto agora, senão trocar de um pro
// outro (ex.: Coordenador A -> Coordenador B) não atualiza visualmente qual está selecionado.
export function ChildNodeSelect({
  nodes,
  selectedNodeId,
  onSelect,
}: {
  nodes: HierarchyNode[];
  selectedNodeId: number | null;
  onSelect: (nodeId: number) => void;
}) {
  if (nodes.length === 0) return null;

  const isChildSelected = nodes.some((node) => node.id === selectedNodeId);

  return (
    <div className="field-inline">
      <label className="field-label" htmlFor="child-node-select">
        {nodes[0].level_display}
      </label>
      <select
        id="child-node-select"
        value={isChildSelected ? String(selectedNodeId) : ""}
        onChange={(e) => {
          if (e.target.value) onSelect(Number(e.target.value));
        }}
      >
        <option value="" disabled>
          Selecionar...
        </option>
        {nodes.map((node) => (
          <option key={node.id} value={node.id}>
            {node.nome}
          </option>
        ))}
      </select>
    </div>
  );
}

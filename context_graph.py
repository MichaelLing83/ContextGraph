"""
Context Graph 构建模块
======================
基于 Zep/Graphiti 和 A-MEM 的设计，为 coding 场景构建 Context Graph

参考论文:
- Zep: A Temporal Knowledge Graph Architecture for Agent Memory
- A-MEM: Agentic Memory for LLM Agents
- Context Graph (Xu et al., 2024)
"""

import json
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple, Any, Set
from datetime import datetime
from collections import defaultdict
import hashlib
import logging

logger = logging.getLogger(__name__)


# ============================================================================
# Graph Schema 定义 (参考 Zep 的三层架构)
# ============================================================================

@dataclass
class EpisodeNode:
    """
    Episode 层节点 - 原始交互记录
    参考 Zep: Episodes serve as a non-lossy data store
    """
    node_id: str
    node_type: str = "episode"
    
    # 内容
    content: str = ""
    action: str = ""
    observation: str = ""
    
    # 元数据
    instance_id: str = ""
    step_index: int = 0
    timestamp: Optional[datetime] = None
    
    # 状态
    state: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict:
        return {
            'node_id': self.node_id,
            'node_type': self.node_type,
            'content': self.content,
            'action': self.action,
            'observation': self.observation,
            'instance_id': self.instance_id,
            'step_index': self.step_index,
            'timestamp': self.timestamp.isoformat() if self.timestamp else None,
            'state': self.state,
        }


@dataclass
class SemanticNode:
    """
    Semantic 层节点 - 代码实体
    包含: File, Function, Class, Variable, ErrorPattern, Module
    """
    node_id: str
    node_type: str  # file, function, class, variable, error_pattern, module
    
    # 基本信息
    name: str = ""
    summary: str = ""  # 实体摘要 (参考 Zep 的 entity summary)
    
    # 代码特定属性
    file_path: Optional[str] = None
    line_range: Optional[Tuple[int, int]] = None
    signature: Optional[str] = None  # 函数签名
    
    # 上下文 (参考 Context Graph 的 entity context)
    context: Dict[str, Any] = field(default_factory=dict)
    
    # 嵌入向量
    embedding: Optional[List[float]] = None
    
    # 时间属性 (参考 Zep 的 bi-temporal 设计)
    t_created: Optional[datetime] = None
    t_valid: Optional[datetime] = None
    
    def to_dict(self) -> Dict:
        return {
            'node_id': self.node_id,
            'node_type': self.node_type,
            'name': self.name,
            'summary': self.summary,
            'file_path': self.file_path,
            'line_range': self.line_range,
            'signature': self.signature,
            'context': self.context,
        }


@dataclass
class CommunityNode:
    """
    Community 层节点 - 高阶概念
    例如: 认证模块、数据库连接、某种常见 bug pattern
    参考 Zep: Communities contain high-level summarizations
    """
    node_id: str
    node_type: str = "community"
    
    name: str = ""
    summary: str = ""
    keywords: List[str] = field(default_factory=list)
    
    # 成员实体
    member_ids: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict:
        return {
            'node_id': self.node_id,
            'node_type': self.node_type,
            'name': self.name,
            'summary': self.summary,
            'keywords': self.keywords,
            'member_ids': self.member_ids,
        }


@dataclass
class Edge:
    """
    边 - 携带丰富上下文
    参考 Context Graph: relation contexts include temporal information, 
    geographic locations, quantitative data, provenance information, etc.
    """
    edge_id: str
    edge_type: str  # CALLS, IMPORTS, MODIFIES, CAUSED_BY, SIMILAR_TO, EVOLVED_FROM, etc.
    
    source_id: str
    target_id: str
    
    # 关系上下文 (参考 Context Graph 的 relation context)
    fact: str = ""  # 关系的文本描述
    context: Dict[str, Any] = field(default_factory=dict)
    
    # 时间属性 (参考 Zep)
    t_created: Optional[datetime] = None
    t_valid: Optional[datetime] = None
    t_invalid: Optional[datetime] = None  # 用于 edge invalidation
    
    # 来源追溯
    source_episode_ids: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict:
        return {
            'edge_id': self.edge_id,
            'edge_type': self.edge_type,
            'source_id': self.source_id,
            'target_id': self.target_id,
            'fact': self.fact,
            'context': self.context,
            't_valid': self.t_valid.isoformat() if self.t_valid else None,
            't_invalid': self.t_invalid.isoformat() if self.t_invalid else None,
            'source_episode_ids': self.source_episode_ids,
        }


# ============================================================================
# Context Graph 主类
# ============================================================================

class ContextGraph:
    """
    Context Graph 实现
    结合 Zep 的三层架构和 A-MEM 的动态演化机制
    """
    
    def __init__(self):
        # 三层节点存储
        self.episode_nodes: Dict[str, EpisodeNode] = {}
        self.semantic_nodes: Dict[str, SemanticNode] = {}
        self.community_nodes: Dict[str, CommunityNode] = {}
        
        # 边存储
        self.edges: Dict[str, Edge] = {}
        
        # 索引
        self._name_to_node: Dict[str, str] = {}  # name -> node_id
        self._type_index: Dict[str, Set[str]] = defaultdict(set)  # type -> node_ids
        self._episode_to_semantic: Dict[str, Set[str]] = defaultdict(set)  # episode -> semantic nodes
        
        # ID 生成器
        self._id_counter = 0
    
    # ------------------------------------------------------------------------
    # 节点操作
    # ------------------------------------------------------------------------
    
    def add_episode(self, episode: EpisodeNode) -> str:
        """添加 Episode 节点"""
        self.episode_nodes[episode.node_id] = episode
        return episode.node_id
    
    def add_semantic_node(self, node: SemanticNode) -> str:
        """
        添加 Semantic 节点
        包含实体解析 (参考 Zep 的 entity resolution)
        """
        # 检查是否已存在相同实体
        existing_id = self._resolve_entity(node)
        if existing_id:
            # 合并信息
            self._merge_semantic_node(existing_id, node)
            return existing_id
        
        # 添加新节点
        self.semantic_nodes[node.node_id] = node
        self._name_to_node[node.name] = node.node_id
        self._type_index[node.node_type].add(node.node_id)
        
        return node.node_id
    
    def add_community(self, community: CommunityNode) -> str:
        """添加 Community 节点"""
        self.community_nodes[community.node_id] = community
        return community.node_id
    
    def _resolve_entity(self, node: SemanticNode) -> Optional[str]:
        """
        实体解析 - 判断是否与已有实体重复
        参考 Zep 的 entity resolution prompt
        """
        # 简单的基于名称匹配
        if node.name in self._name_to_node:
            existing_id = self._name_to_node[node.name]
            existing = self.semantic_nodes.get(existing_id)
            
            if existing and existing.node_type == node.node_type:
                # 同类型同名，认为是同一实体
                return existing_id
            
            # 如果有 file_path，进一步检查
            if node.file_path and existing and existing.file_path == node.file_path:
                return existing_id
        
        return None
    
    def _merge_semantic_node(self, existing_id: str, new_node: SemanticNode):
        """合并语义节点信息"""
        existing = self.semantic_nodes[existing_id]
        
        # 更新摘要（如果新的更详细）
        if len(new_node.summary) > len(existing.summary):
            existing.summary = new_node.summary
        
        # 合并上下文
        existing.context.update(new_node.context)
        
        # 更新行范围
        if new_node.line_range:
            existing.line_range = new_node.line_range
    
    # ------------------------------------------------------------------------
    # 边操作
    # ------------------------------------------------------------------------
    
    def add_edge(self, edge: Edge) -> str:
        """
        添加边
        包含边去重和 invalidation 检查 (参考 Zep)
        """
        # 检查是否存在相似边
        existing_edge = self._find_similar_edge(edge)
        
        if existing_edge:
            # 检查是否需要 invalidate 旧边
            if self._should_invalidate(existing_edge, edge):
                self._invalidate_edge(existing_edge.edge_id, edge.t_valid)
            else:
                # 合并信息
                existing_edge.context.update(edge.context)
                existing_edge.source_episode_ids.extend(edge.source_episode_ids)
                return existing_edge.edge_id
        
        # 添加新边
        self.edges[edge.edge_id] = edge
        return edge.edge_id
    
    def _find_similar_edge(self, edge: Edge) -> Optional[Edge]:
        """查找相似边"""
        for existing in self.edges.values():
            if (existing.source_id == edge.source_id and 
                existing.target_id == edge.target_id and
                existing.edge_type == edge.edge_type and
                existing.t_invalid is None):  # 只考虑有效边
                return existing
        return None
    
    def _should_invalidate(self, old_edge: Edge, new_edge: Edge) -> bool:
        """
        判断是否需要 invalidate 旧边
        参考 Zep: 当新边与旧边矛盾时
        """
        # 这里可以添加更复杂的矛盾检测逻辑
        # 目前简单处理：如果 fact 不同，认为可能矛盾
        if old_edge.fact and new_edge.fact and old_edge.fact != new_edge.fact:
            return True
        return False
    
    def _invalidate_edge(self, edge_id: str, invalid_time: Optional[datetime] = None):
        """
        Invalidate 边
        参考 Zep: setting their t_invalid to the t_valid of the invalidating edge
        """
        edge = self.edges.get(edge_id)
        if edge:
            edge.t_invalid = invalid_time or datetime.now()
            logger.info(f"Invalidated edge {edge_id}")
    
    # ------------------------------------------------------------------------
    # Link Generation (参考 A-MEM)
    # ------------------------------------------------------------------------
    
    def generate_links(self, new_node: SemanticNode, top_k: int = 5) -> List[Edge]:
        """
        为新节点生成链接
        参考 A-MEM: 基于 embedding 召回 top-k 相似节点，然后判断是否建立连接
        """
        # 1. 找到最相似的节点 (这里用简单的名称相似度，实际应用中用 embedding)
        similar_nodes = self._find_similar_nodes(new_node, top_k)
        
        # 2. 生成连接
        new_edges = []
        for similar_node, similarity in similar_nodes:
            # 判断是否应该建立连接
            edge = self._create_link_if_appropriate(new_node, similar_node, similarity)
            if edge:
                new_edges.append(edge)
                self.add_edge(edge)
        
        return new_edges
    
    def _find_similar_nodes(self, node: SemanticNode, top_k: int) -> List[Tuple[SemanticNode, float]]:
        """找到最相似的节点"""
        similarities = []
        
        for existing in self.semantic_nodes.values():
            if existing.node_id == node.node_id:
                continue
            
            # 计算相似度 (简化版：基于共同属性)
            sim = self._compute_similarity(node, existing)
            if sim > 0.1:  # 阈值
                similarities.append((existing, sim))
        
        # 排序取 top-k
        similarities.sort(key=lambda x: -x[1])
        return similarities[:top_k]
    
    def _compute_similarity(self, node1: SemanticNode, node2: SemanticNode) -> float:
        """计算节点相似度"""
        score = 0.0
        
        # 同类型加分
        if node1.node_type == node2.node_type:
            score += 0.3
        
        # 同文件加分
        if node1.file_path and node1.file_path == node2.file_path:
            score += 0.4
        
        # 名称相似度
        if node1.name and node2.name:
            common = set(node1.name.lower()) & set(node2.name.lower())
            all_chars = set(node1.name.lower()) | set(node2.name.lower())
            if all_chars:
                score += 0.3 * len(common) / len(all_chars)
        
        return score
    
    def _create_link_if_appropriate(self, node1: SemanticNode, node2: SemanticNode, 
                                     similarity: float) -> Optional[Edge]:
        """判断是否创建链接"""
        # 根据节点类型确定关系类型
        edge_type = self._infer_edge_type(node1, node2)
        
        if edge_type:
            return Edge(
                edge_id=self._generate_id('edge'),
                edge_type=edge_type,
                source_id=node1.node_id,
                target_id=node2.node_id,
                fact=f"{node1.name} {edge_type} {node2.name}",
                context={'similarity': similarity},
                t_created=datetime.now(),
                t_valid=datetime.now(),
            )
        
        return None
    
    def _infer_edge_type(self, node1: SemanticNode, node2: SemanticNode) -> Optional[str]:
        """推断边类型"""
        t1, t2 = node1.node_type, node2.node_type
        
        type_map = {
            ('function', 'function'): 'CALLS',
            ('file', 'module'): 'IMPORTS',
            ('function', 'file'): 'DEFINED_IN',
            ('class', 'file'): 'DEFINED_IN',
            ('error_pattern', 'function'): 'RAISED_BY',
            ('error_pattern', 'file'): 'OCCURS_IN',
        }
        
        return type_map.get((t1, t2)) or type_map.get((t2, t1)) or 'RELATED_TO'
    
    # ------------------------------------------------------------------------
    # Memory Evolution (参考 A-MEM)
    # ------------------------------------------------------------------------
    
    def evolve_memory(self, new_node: SemanticNode, related_nodes: List[SemanticNode]):
        """
        Memory Evolution
        参考 A-MEM: 新节点加入时，更新相关旧节点的 context
        """
        for related in related_nodes:
            # 更新 context，添加与新节点的关联信息
            if 'related_entities' not in related.context:
                related.context['related_entities'] = []
            
            related.context['related_entities'].append({
                'node_id': new_node.node_id,
                'name': new_node.name,
                'added_at': datetime.now().isoformat(),
            })
            
            # 更新 summary
            # 实际应用中可以用 LLM 生成更好的 summary
            if new_node.summary:
                related.context['latest_related_info'] = new_node.summary[:200]
    
    # ------------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------------
    
    def get_nodes_by_type(self, node_type: str) -> List[SemanticNode]:
        """按类型获取节点"""
        node_ids = self._type_index.get(node_type, set())
        return [self.semantic_nodes[nid] for nid in node_ids if nid in self.semantic_nodes]
    
    def get_node_neighbors(self, node_id: str) -> List[Tuple[str, Edge]]:
        """获取节点的邻居"""
        neighbors = []
        for edge in self.edges.values():
            if edge.t_invalid:  # 跳过已失效的边
                continue
            if edge.source_id == node_id:
                neighbors.append((edge.target_id, edge))
            elif edge.target_id == node_id:
                neighbors.append((edge.source_id, edge))
        return neighbors
    
    def bfs_subgraph(self, start_node_id: str, max_hops: int = 2) -> Dict:
        """
        BFS 获取子图
        参考 Zep: breadth-first search over knowledge graphs
        """
        visited = {start_node_id}
        subgraph = {
            'nodes': [],
            'edges': [],
        }
        
        current_level = {start_node_id}
        
        for _ in range(max_hops):
            next_level = set()
            for node_id in current_level:
                node = self.semantic_nodes.get(node_id)
                if node:
                    subgraph['nodes'].append(node.to_dict())
                
                for neighbor_id, edge in self.get_node_neighbors(node_id):
                    if neighbor_id not in visited:
                        next_level.add(neighbor_id)
                        visited.add(neighbor_id)
                    subgraph['edges'].append(edge.to_dict())
            
            current_level = next_level
            if not current_level:
                break
        
        return subgraph
    
    # ------------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------------
    
    def _generate_id(self, prefix: str = 'node') -> str:
        """生成唯一 ID"""
        self._id_counter += 1
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        return f"{prefix}_{timestamp}_{self._id_counter}"
    
    def to_dict(self) -> Dict:
        """导出为字典"""
        return {
            'episode_nodes': [n.to_dict() for n in self.episode_nodes.values()],
            'semantic_nodes': [n.to_dict() for n in self.semantic_nodes.values()],
            'community_nodes': [n.to_dict() for n in self.community_nodes.values()],
            'edges': [e.to_dict() for e in self.edges.values() if e.t_invalid is None],
            'invalidated_edges': [e.to_dict() for e in self.edges.values() if e.t_invalid is not None],
        }
    
    def save(self, path: Path):
        """保存到文件"""
        with open(path, 'w') as f:
            json.dump(self.to_dict(), f, indent=2, default=str)
    
    def load(self, path: Path):
        """从文件加载"""
        with open(path, 'r') as f:
            data = json.load(f)
        
        # 加载节点
        for node_data in data.get('semantic_nodes', []):
            node = SemanticNode(
                node_id=node_data['node_id'],
                node_type=node_data['node_type'],
                name=node_data.get('name', ''),
                summary=node_data.get('summary', ''),
                file_path=node_data.get('file_path'),
                context=node_data.get('context', {}),
            )
            self.semantic_nodes[node.node_id] = node
            self._name_to_node[node.name] = node.node_id
            self._type_index[node.node_type].add(node.node_id)
        
        # 加载边
        for edge_data in data.get('edges', []):
            edge = Edge(
                edge_id=edge_data['edge_id'],
                edge_type=edge_data['edge_type'],
                source_id=edge_data['source_id'],
                target_id=edge_data['target_id'],
                fact=edge_data.get('fact', ''),
                context=edge_data.get('context', {}),
            )
            self.edges[edge.edge_id] = edge
    
    def stats(self) -> Dict:
        """统计信息"""
        return {
            'num_episode_nodes': len(self.episode_nodes),
            'num_semantic_nodes': len(self.semantic_nodes),
            'num_community_nodes': len(self.community_nodes),
            'num_edges': len(self.edges),
            'num_valid_edges': sum(1 for e in self.edges.values() if e.t_invalid is None),
            'node_type_distribution': {t: len(ids) for t, ids in self._type_index.items()},
            'edge_type_distribution': self._edge_type_distribution(),
        }
    
    def _edge_type_distribution(self) -> Dict[str, int]:
        dist = defaultdict(int)
        for edge in self.edges.values():
            if edge.t_invalid is None:
                dist[edge.edge_type] += 1
        return dict(dist)


# ============================================================================
# Graph Builder - 从轨迹构建图
# ============================================================================

class GraphBuilder:
    """从轨迹构建 Context Graph"""
    
    def __init__(self):
        self.graph = ContextGraph()
    
    def build_from_trajectory(self, trajectory) -> ContextGraph:
        """从单个轨迹构建图"""
        # trajectory 是 analyzer.Trajectory 类型，但我们直接使用其属性即可
        
        # 1. 添加 Episode 节点
        for step in trajectory.steps:
            episode = EpisodeNode(
                node_id=f"ep_{trajectory.instance_id}_{step.step_id}",
                content=step.thought,
                action=step.action,
                observation=step.observation,
                instance_id=trajectory.instance_id,
                step_index=step.step_id,
                state=step.state,
            )
            self.graph.add_episode(episode)
        
        # 2. 添加 Semantic 节点
        for entity in trajectory.entities:
            node = SemanticNode(
                node_id=entity.entity_id,
                node_type=entity.entity_type,
                name=entity.name,
                file_path=entity.file_path,
                context=entity.attributes,
                t_created=datetime.now(),
            )
            self.graph.add_semantic_node(node)
            
            # 生成链接
            self.graph.generate_links(node)
        
        # 3. 添加关系边
        for relation in trajectory.relations:
            edge = Edge(
                edge_id=relation.relation_id,
                edge_type=relation.relation_type,
                source_id=relation.source_id,
                target_id=relation.target_id,
                context=relation.context,
                t_created=datetime.now(),
                t_valid=datetime.now(),
            )
            self.graph.add_edge(edge)
        
        return self.graph
    
    def build_from_trajectories(self, trajectories: List) -> ContextGraph:
        """从多个轨迹构建图"""
        for traj in trajectories:
            self.build_from_trajectory(traj)
        return self.graph


# ============================================================================
# 示例使用
# ============================================================================

if __name__ == '__main__':
    # 创建图
    graph = ContextGraph()
    
    # 添加一些示例节点
    file_node = SemanticNode(
        node_id='file_001',
        node_type='file',
        name='fields.py',
        file_path='src/marshmallow/fields.py',
        summary='Contains field definitions for marshmallow',
    )
    graph.add_semantic_node(file_node)
    
    func_node = SemanticNode(
        node_id='func_001',
        node_type='function',
        name='_serialize',
        file_path='src/marshmallow/fields.py',
        line_range=(1471, 1475),
        signature='def _serialize(self, value, attr, obj, **kwargs)',
    )
    graph.add_semantic_node(func_node)
    
    error_node = SemanticNode(
        node_id='error_001',
        node_type='error_pattern',
        name='TypeError',
        summary='Integer division error in timedelta serialization',
    )
    graph.add_semantic_node(error_node)
    
    # 添加边
    graph.add_edge(Edge(
        edge_id='edge_001',
        edge_type='DEFINED_IN',
        source_id='func_001',
        target_id='file_001',
        fact='_serialize is defined in fields.py',
    ))
    
    graph.add_edge(Edge(
        edge_id='edge_002',
        edge_type='RAISED_BY',
        source_id='error_001',
        target_id='func_001',
        fact='TypeError is raised by _serialize function',
        context={'line': 1474},
    ))
    
    # 生成链接
    new_links = graph.generate_links(error_node)
    print(f"Generated {len(new_links)} new links")
    
    # 打印统计
    print("\nGraph statistics:")
    for k, v in graph.stats().items():
        print(f"  {k}: {v}")
    
    # 获取子图
    subgraph = graph.bfs_subgraph('func_001', max_hops=2)
    print(f"\nSubgraph from func_001: {len(subgraph['nodes'])} nodes, {len(subgraph['edges'])} edges")

"""
SWE-bench Trajectory Analyzer
=============================
分析 SWE-bench agent 轨迹，提取用于 Context Graph 构建的模式和实体

主要功能:
1. 加载和解析轨迹数据 (SWE-agent, OpenHands 等格式)
2. 提取代码实体 (文件、函数、类、变量、错误模式)
3. 提取动作序列和状态转换
4. 统计分析成功/失败案例的差异
5. 导出为 Context Graph 构建所需的格式
"""

import json
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple, Any
from collections import defaultdict
from datetime import datetime
import hashlib


# ============================================================================
# 数据结构定义 (参考 Zep/Graphiti 和 A-MEM 的设计)
# ============================================================================

@dataclass
class CodeEntity:
    """代码实体 - 对应 Context Graph 中的顶点"""
    entity_id: str
    entity_type: str  # file, function, class, variable, error_pattern, module
    name: str
    file_path: Optional[str] = None
    line_range: Optional[Tuple[int, int]] = None
    content_summary: str = ""
    attributes: Dict[str, Any] = field(default_factory=dict)
    embedding: Optional[List[float]] = None
    
    # 时间属性 (参考 Zep 的 bi-temporal 设计)
    t_created: Optional[datetime] = None  # 系统记录时间
    t_valid: Optional[datetime] = None    # 事实有效时间
    
    def to_dict(self) -> Dict:
        return {
            "entity_id": self.entity_id,
            "entity_type": self.entity_type,
            "name": self.name,
            "file_path": self.file_path,
            "line_range": self.line_range,
            "content_summary": self.content_summary,
            "attributes": self.attributes,
        }


@dataclass
class CodeRelation:
    """代码关系 - 对应 Context Graph 中的边"""
    relation_id: str
    relation_type: str  # CALLS, IMPORTS, MODIFIES, CAUSED_BY, SIMILAR_TO, etc.
    source_id: str
    target_id: str
    
    # 关系上下文 (参考 Context Graph 论文的 relation context)
    context: Dict[str, Any] = field(default_factory=dict)
    
    # 时间属性
    t_created: Optional[datetime] = None
    t_valid: Optional[datetime] = None
    t_invalid: Optional[datetime] = None  # 用于 edge invalidation
    
    def to_dict(self) -> Dict:
        return {
            "relation_id": self.relation_id,
            "relation_type": self.relation_type,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "context": self.context,
        }


@dataclass
class ActionStep:
    """单个动作步骤 - 对应 Episode 层"""
    step_id: int
    thought: str
    action: str
    action_type: str  # search, edit, run_test, explore, generate_fix, etc.
    observation: str
    state: Dict[str, Any] = field(default_factory=dict)
    
    # 提取的实体和关系
    entities_mentioned: List[str] = field(default_factory=list)
    files_accessed: List[str] = field(default_factory=list)
    code_changes: List[Dict] = field(default_factory=list)
    errors_encountered: List[str] = field(default_factory=list)
    
    timestamp: Optional[datetime] = None


@dataclass 
class Trajectory:
    """完整轨迹"""
    instance_id: str
    repo: str
    problem_statement: str
    steps: List[ActionStep] = field(default_factory=list)
    
    # 结果
    is_resolved: bool = False
    final_patch: Optional[str] = None
    gold_patch: Optional[str] = None
    
    # 统计
    total_steps: int = 0
    action_distribution: Dict[str, int] = field(default_factory=dict)
    
    # 提取的图结构
    entities: List[CodeEntity] = field(default_factory=list)
    relations: List[CodeRelation] = field(default_factory=list)


# ============================================================================
# 轨迹解析器
# ============================================================================

class TrajectoryParser:
    """解析不同格式的轨迹文件"""
    
    # 动作类型分类 (参考论文中的分类)
    ACTION_CATEGORIES = {
        'search': ['find', 'search', 'grep', 'locate', 'ag ', 'rg '],
        'explore': ['ls', 'cat', 'head', 'tail', 'tree', 'pwd', 'cd'],
        'edit': ['edit', 'sed', 'vim', 'nano', 'patch', 'write'],
        'run_test': ['pytest', 'python -m test', 'make test', 'npm test', 'cargo test'],
        'generate_fix': ['submit', 'apply_patch', 'git diff'],
        'reproduce': ['python', 'node', 'execute', 'run'],
        'navigate': ['open', 'goto', 'scroll'],
    }
    
    def __init__(self):
        self.entity_extractor = EntityExtractor()
    
    def parse_swe_agent_trajectory(self, traj_path: Path) -> Trajectory:
        """解析 SWE-agent 格式的轨迹"""
        with open(traj_path, 'r') as f:
            data = json.load(f)
        
        # 提取基本信息
        instance_id = traj_path.stem
        
        trajectory = Trajectory(
            instance_id=instance_id,
            repo=data.get('environment', ''),
            problem_statement=self._extract_problem_statement(data),
        )
        
        # 解析每个步骤
        raw_trajectory = data.get('trajectory', [])
        for idx, step_data in enumerate(raw_trajectory):
            step = self._parse_step(idx, step_data)
            trajectory.steps.append(step)
        
        trajectory.total_steps = len(trajectory.steps)
        trajectory.action_distribution = self._compute_action_distribution(trajectory.steps)
        
        # 提取实体和关系
        self._extract_entities_and_relations(trajectory)
        
        return trajectory
    
    def parse_openhands_trajectory(self, traj_data: Dict) -> Trajectory:
        """解析 OpenHands 格式的轨迹"""
        trajectory = Trajectory(
            instance_id=traj_data.get('instance_id', ''),
            repo=traj_data.get('repo', ''),
            problem_statement=traj_data.get('problem_statement', ''),
        )
        
        # OpenHands 使用 tool_calls 格式
        messages = traj_data.get('trajectory', [])
        step_idx = 0
        
        for msg in messages:
            if msg.get('role') == 'assistant' and msg.get('tool_calls'):
                for tool_call in msg['tool_calls']:
                    step = self._parse_openhands_tool_call(step_idx, tool_call, msg)
                    trajectory.steps.append(step)
                    step_idx += 1
        
        trajectory.total_steps = len(trajectory.steps)
        self._extract_entities_and_relations(trajectory)
        
        return trajectory
    
    def _parse_step(self, idx: int, step_data: Dict) -> ActionStep:
        """解析单个步骤"""
        action = step_data.get('action', '')
        thought = step_data.get('thought', step_data.get('response', ''))
        observation = step_data.get('observation', '')
        
        # 解析 state
        state_str = step_data.get('state', '{}')
        if isinstance(state_str, str):
            try:
                state = json.loads(state_str)
            except:
                state = {}
        else:
            state = state_str
        
        step = ActionStep(
            step_id=idx,
            thought=thought,
            action=action,
            action_type=self._classify_action(action),
            observation=observation,
            state=state,
        )
        
        # 提取文件访问
        step.files_accessed = self._extract_files_from_text(action + observation)
        
        # 提取错误
        step.errors_encountered = self._extract_errors(observation)
        
        # 提取代码变更
        if 'edit' in action.lower() or 'patch' in action.lower():
            step.code_changes = self._extract_code_changes(action, observation)
        
        return step
    
    def _parse_openhands_tool_call(self, idx: int, tool_call: Dict, msg: Dict) -> ActionStep:
        """解析 OpenHands 的 tool_call"""
        func = tool_call.get('function', {})
        action = f"{func.get('name', '')}({func.get('arguments', '')})"
        
        return ActionStep(
            step_id=idx,
            thought=msg.get('content', ''),
            action=action,
            action_type=self._classify_action(func.get('name', '')),
            observation='',  # 需要从后续 tool response 中获取
            state={},
        )
    
    def _classify_action(self, action: str) -> str:
        """分类动作类型"""
        action_lower = action.lower()
        for category, patterns in self.ACTION_CATEGORIES.items():
            if any(p in action_lower for p in patterns):
                return category
        return 'other'
    
    def _extract_problem_statement(self, data: Dict) -> str:
        """提取问题描述"""
        # 从 query 中查找 user 消息
        trajectory = data.get('trajectory', [])
        if trajectory and 'query' in trajectory[0]:
            for msg in trajectory[0]['query']:
                if msg.get('role') == 'user':
                    return msg.get('content', '')[:2000]
        return ''
    
    def _extract_files_from_text(self, text: str) -> List[str]:
        """从文本中提取文件路径"""
        # 匹配常见文件路径模式
        patterns = [
            r'[\w\-./]+\.py',
            r'[\w\-./]+\.js',
            r'[\w\-./]+\.ts',
            r'[\w\-./]+\.java',
            r'[\w\-./]+\.rs',
            r'[\w\-./]+\.go',
            r'[\w\-./]+\.cpp',
            r'[\w\-./]+\.c',
            r'[\w\-./]+\.h',
            r'[\w\-./]+\.yaml',
            r'[\w\-./]+\.json',
            r'[\w\-./]+\.toml',
        ]
        
        files = set()
        for pattern in patterns:
            matches = re.findall(pattern, text)
            files.update(m for m in matches if '/' in m or m.count('.') == 1)
        
        return list(files)
    
    def _extract_errors(self, observation: str) -> List[str]:
        """提取错误信息"""
        error_patterns = [
            r'Error:.*',
            r'Exception:.*',
            r'Traceback.*',
            r'FAILED.*',
            r'AssertionError.*',
            r'TypeError.*',
            r'ValueError.*',
            r'ImportError.*',
            r'AttributeError.*',
            r'NameError.*',
            r'SyntaxError.*',
        ]
        
        errors = []
        for pattern in error_patterns:
            matches = re.findall(pattern, observation, re.IGNORECASE)
            errors.extend(matches[:3])  # 限制每种错误最多3个
        
        return errors
    
    def _extract_code_changes(self, action: str, observation: str) -> List[Dict]:
        """提取代码变更"""
        changes = []
        
        # 解析 edit 命令
        edit_match = re.search(r'edit\s+(\d+):(\d+)', action)
        if edit_match:
            changes.append({
                'type': 'edit',
                'start_line': int(edit_match.group(1)),
                'end_line': int(edit_match.group(2)),
            })
        
        # 解析 diff 输出
        diff_pattern = r'@@ -(\d+),?(\d*) \+(\d+),?(\d*) @@'
        for match in re.finditer(diff_pattern, observation):
            changes.append({
                'type': 'diff',
                'old_start': int(match.group(1)),
                'new_start': int(match.group(3)),
            })
        
        return changes
    
    def _compute_action_distribution(self, steps: List[ActionStep]) -> Dict[str, int]:
        """计算动作类型分布"""
        distribution = defaultdict(int)
        for step in steps:
            distribution[step.action_type] += 1
        return dict(distribution)
    
    def _extract_entities_and_relations(self, trajectory: Trajectory):
        """从轨迹中提取实体和关系"""
        trajectory.entities, trajectory.relations = \
            self.entity_extractor.extract_from_trajectory(trajectory)


# ============================================================================
# 实体和关系提取器
# ============================================================================

class EntityExtractor:
    """从轨迹中提取代码实体和关系"""
    
    def __init__(self):
        self.entity_cache: Dict[str, CodeEntity] = {}
        self.entity_counter = 0
    
    def extract_from_trajectory(self, trajectory: Trajectory) -> Tuple[List[CodeEntity], List[CodeRelation]]:
        """从轨迹中提取所有实体和关系"""
        entities = []
        relations = []
        
        # 1. 从 problem statement 提取
        problem_entities = self._extract_from_problem(trajectory.problem_statement)
        entities.extend(problem_entities)
        
        # 2. 从每个步骤提取
        prev_step_entities = []
        for step in trajectory.steps:
            step_entities, step_relations = self._extract_from_step(step, prev_step_entities)
            entities.extend(step_entities)
            relations.extend(step_relations)
            prev_step_entities = step_entities
        
        # 3. 从 patch 提取
        if trajectory.final_patch:
            patch_entities = self._extract_from_patch(trajectory.final_patch)
            entities.extend(patch_entities)
        
        # 去重
        entities = self._deduplicate_entities(entities)
        
        return entities, relations
    
    def _extract_from_problem(self, problem: str) -> List[CodeEntity]:
        """从问题描述中提取实体"""
        entities = []
        
        # 提取提到的文件
        files = self._extract_file_references(problem)
        for f in files:
            entities.append(self._create_entity('file', f, file_path=f))
        
        # 提取提到的函数/方法
        functions = self._extract_function_references(problem)
        for func in functions:
            entities.append(self._create_entity('function', func))
        
        # 提取错误类型
        errors = self._extract_error_types(problem)
        for err in errors:
            entities.append(self._create_entity('error_pattern', err))
        
        return entities
    
    def _extract_from_step(self, step: ActionStep, prev_entities: List[CodeEntity]) -> Tuple[List[CodeEntity], List[CodeRelation]]:
        """从单个步骤提取实体和关系"""
        entities = []
        relations = []
        
        # 提取文件实体
        for file_path in step.files_accessed:
            entity = self._create_entity('file', file_path, file_path=file_path)
            entities.append(entity)
        
        # 提取错误实体
        for error in step.errors_encountered:
            entity = self._create_entity('error_pattern', error[:100])
            entities.append(entity)
            
            # 创建 CAUSED_BY 关系
            if step.files_accessed:
                for file_path in step.files_accessed:
                    file_entity = self._get_or_create_entity('file', file_path)
                    relations.append(CodeRelation(
                        relation_id=f"rel_{len(relations)}",
                        relation_type='CAUSED_BY',
                        source_id=entity.entity_id,
                        target_id=file_entity.entity_id,
                        context={'step_id': step.step_id, 'action': step.action_type}
                    ))
        
        # 创建 MODIFIES 关系
        for change in step.code_changes:
            for file_path in step.files_accessed:
                file_entity = self._get_or_create_entity('file', file_path)
                relations.append(CodeRelation(
                    relation_id=f"rel_{len(relations)}",
                    relation_type='MODIFIES',
                    source_id=f"action_{step.step_id}",
                    target_id=file_entity.entity_id,
                    context={'change': change, 'action_type': step.action_type}
                ))
        
        return entities, relations
    
    def _extract_from_patch(self, patch: str) -> List[CodeEntity]:
        """从 patch 中提取实体"""
        entities = []
        
        # 提取修改的文件
        file_pattern = r'diff --git a/(.*?) b/'
        files = re.findall(file_pattern, patch)
        for f in files:
            entities.append(self._create_entity('file', f, file_path=f, 
                                                 attributes={'modified': True}))
        
        # 提取修改的函数 (通过 @@ 行)
        func_pattern = r'@@ .* @@ (?:def|class|function|fn) (\w+)'
        functions = re.findall(func_pattern, patch)
        for func in functions:
            entities.append(self._create_entity('function', func,
                                                 attributes={'modified': True}))
        
        return entities
    
    def _extract_file_references(self, text: str) -> List[str]:
        """提取文本中的文件引用"""
        patterns = [
            r'`([^`]+\.\w+)`',  # 反引号包裹的文件名
            r'[\w\-./]+\.py',
            r'[\w\-./]+\.js',
            r'[\w\-./]+\.ts',
        ]
        files = set()
        for p in patterns:
            files.update(re.findall(p, text))
        return list(files)
    
    def _extract_function_references(self, text: str) -> List[str]:
        """提取文本中的函数引用"""
        patterns = [
            r'`(\w+)\(\)`',  # 反引号包裹的函数调用
            r'function\s+(\w+)',
            r'def\s+(\w+)',
            r'method\s+(\w+)',
            r'(\w+)\(\)',  # 一般函数调用
        ]
        functions = set()
        for p in patterns:
            matches = re.findall(p, text)
            functions.update(m for m in matches if len(m) > 2 and m.isidentifier())
        return list(functions)
    
    def _extract_error_types(self, text: str) -> List[str]:
        """提取错误类型"""
        patterns = [
            r'(\w+Error)',
            r'(\w+Exception)',
            r'(\w+Warning)',
        ]
        errors = set()
        for p in patterns:
            errors.update(re.findall(p, text))
        return list(errors)
    
    def _create_entity(self, entity_type: str, name: str, **kwargs) -> CodeEntity:
        """创建实体"""
        entity_id = self._generate_entity_id(entity_type, name)
        return CodeEntity(
            entity_id=entity_id,
            entity_type=entity_type,
            name=name,
            t_created=datetime.now(),
            **kwargs
        )
    
    def _get_or_create_entity(self, entity_type: str, name: str) -> CodeEntity:
        """获取或创建实体"""
        key = f"{entity_type}:{name}"
        if key not in self.entity_cache:
            self.entity_cache[key] = self._create_entity(entity_type, name)
        return self.entity_cache[key]
    
    def _generate_entity_id(self, entity_type: str, name: str) -> str:
        """生成实体 ID"""
        content = f"{entity_type}:{name}"
        return hashlib.md5(content.encode()).hexdigest()[:12]
    
    def _deduplicate_entities(self, entities: List[CodeEntity]) -> List[CodeEntity]:
        """去重实体"""
        seen = {}
        result = []
        for e in entities:
            if e.entity_id not in seen:
                seen[e.entity_id] = e
                result.append(e)
            else:
                # 合并属性
                existing = seen[e.entity_id]
                existing.attributes.update(e.attributes)
        return result


# ============================================================================
# 统计分析器
# ============================================================================

class TrajectoryAnalyzer:
    """轨迹统计分析"""
    
    def __init__(self):
        self.trajectories: List[Trajectory] = []
    
    def add_trajectory(self, traj: Trajectory):
        self.trajectories.append(traj)
    
    def compute_statistics(self) -> Dict:
        """计算整体统计"""
        if not self.trajectories:
            return {}
        
        resolved = [t for t in self.trajectories if t.is_resolved]
        unresolved = [t for t in self.trajectories if not t.is_resolved]
        
        stats = {
            'total_trajectories': len(self.trajectories),
            'resolved_count': len(resolved),
            'unresolved_count': len(unresolved),
            'resolution_rate': len(resolved) / len(self.trajectories) if self.trajectories else 0,
            
            # 步骤统计
            'avg_steps_resolved': self._avg([t.total_steps for t in resolved]),
            'avg_steps_unresolved': self._avg([t.total_steps for t in unresolved]),
            
            # 动作分布
            'action_distribution_resolved': self._merge_distributions([t.action_distribution for t in resolved]),
            'action_distribution_unresolved': self._merge_distributions([t.action_distribution for t in unresolved]),
            
            # 实体统计
            'avg_entities_resolved': self._avg([len(t.entities) for t in resolved]),
            'avg_entities_unresolved': self._avg([len(t.entities) for t in unresolved]),
            
            # 常见实体类型
            'entity_type_distribution': self._compute_entity_type_distribution(),
            
            # 常见关系类型
            'relation_type_distribution': self._compute_relation_type_distribution(),
        }
        
        return stats
    
    def compare_resolved_vs_unresolved(self) -> Dict:
        """对比成功和失败案例"""
        resolved = [t for t in self.trajectories if t.is_resolved]
        unresolved = [t for t in self.trajectories if not t.is_resolved]
        
        comparison = {
            'step_count_difference': {
                'resolved_avg': self._avg([t.total_steps for t in resolved]),
                'unresolved_avg': self._avg([t.total_steps for t in unresolved]),
            },
            'action_pattern_differences': self._compare_action_patterns(resolved, unresolved),
            'entity_coverage_differences': self._compare_entity_coverage(resolved, unresolved),
            'error_handling_differences': self._compare_error_handling(resolved, unresolved),
        }
        
        return comparison
    
    def extract_success_patterns(self) -> List[Dict]:
        """提取成功案例的模式 (参考 ExpeL 论文)"""
        resolved = [t for t in self.trajectories if t.is_resolved]
        patterns = []
        
        # 提取常见的动作序列
        action_sequences = self._extract_action_sequences(resolved)
        for seq, count in action_sequences.most_common(10):
            patterns.append({
                'type': 'action_sequence',
                'pattern': seq,
                'frequency': count,
            })
        
        # 提取成功案例中的关键实体类型
        key_entities = self._extract_key_entities(resolved)
        patterns.append({
            'type': 'key_entities',
            'entities': key_entities,
        })
        
        return patterns
    
    def _avg(self, values: List) -> float:
        return sum(values) / len(values) if values else 0
    
    def _merge_distributions(self, distributions: List[Dict]) -> Dict:
        merged = defaultdict(int)
        for d in distributions:
            for k, v in d.items():
                merged[k] += v
        return dict(merged)
    
    def _compute_entity_type_distribution(self) -> Dict:
        dist = defaultdict(int)
        for t in self.trajectories:
            for e in t.entities:
                dist[e.entity_type] += 1
        return dict(dist)
    
    def _compute_relation_type_distribution(self) -> Dict:
        dist = defaultdict(int)
        for t in self.trajectories:
            for r in t.relations:
                dist[r.relation_type] += 1
        return dict(dist)
    
    def _compare_action_patterns(self, resolved: List, unresolved: List) -> Dict:
        resolved_dist = self._merge_distributions([t.action_distribution for t in resolved])
        unresolved_dist = self._merge_distributions([t.action_distribution for t in unresolved])
        
        # 归一化
        resolved_total = sum(resolved_dist.values()) or 1
        unresolved_total = sum(unresolved_dist.values()) or 1
        
        return {
            'resolved': {k: v/resolved_total for k, v in resolved_dist.items()},
            'unresolved': {k: v/unresolved_total for k, v in unresolved_dist.items()},
        }
    
    def _compare_entity_coverage(self, resolved: List, unresolved: List) -> Dict:
        def get_entity_types(trajs):
            types = defaultdict(int)
            for t in trajs:
                for e in t.entities:
                    types[e.entity_type] += 1
            return dict(types)
        
        return {
            'resolved': get_entity_types(resolved),
            'unresolved': get_entity_types(unresolved),
        }
    
    def _compare_error_handling(self, resolved: List, unresolved: List) -> Dict:
        def count_errors(trajs):
            total = 0
            for t in trajs:
                for step in t.steps:
                    total += len(step.errors_encountered)
            return total / len(trajs) if trajs else 0
        
        return {
            'avg_errors_resolved': count_errors(resolved),
            'avg_errors_unresolved': count_errors(unresolved),
        }
    
    def _extract_action_sequences(self, trajectories: List[Trajectory]):
        from collections import Counter
        sequences = Counter()
        
        for t in trajectories:
            # 提取长度为3的动作序列
            actions = [s.action_type for s in t.steps]
            for i in range(len(actions) - 2):
                seq = tuple(actions[i:i+3])
                sequences[seq] += 1
        
        return sequences
    
    def _extract_key_entities(self, trajectories: List[Trajectory]) -> List[str]:
        entity_freq = defaultdict(int)
        for t in trajectories:
            for e in t.entities:
                entity_freq[e.name] += 1
        
        # 返回频率最高的实体
        sorted_entities = sorted(entity_freq.items(), key=lambda x: -x[1])
        return [e[0] for e in sorted_entities[:20]]


# ============================================================================
# 导出器
# ============================================================================

class GraphExporter:
    """导出为 Context Graph 格式"""
    
    def export_to_json(self, trajectories: List[Trajectory], output_path: Path):
        """导出为 JSON 格式"""
        data = {
            'nodes': [],
            'edges': [],
            'episodes': [],
        }
        
        for traj in trajectories:
            # 添加 episode (轨迹级别)
            episode = {
                'episode_id': traj.instance_id,
                'repo': traj.repo,
                'problem_statement': traj.problem_statement[:500],
                'is_resolved': traj.is_resolved,
                'total_steps': traj.total_steps,
            }
            data['episodes'].append(episode)
            
            # 添加节点
            for entity in traj.entities:
                node = entity.to_dict()
                node['episode_id'] = traj.instance_id
                data['nodes'].append(node)
            
            # 添加边
            for relation in traj.relations:
                edge = relation.to_dict()
                edge['episode_id'] = traj.instance_id
                data['edges'].append(edge)
        
        with open(output_path, 'w') as f:
            json.dump(data, f, indent=2, default=str)
    
    def export_for_graphiti(self, trajectories: List[Trajectory], output_path: Path):
        """导出为 Graphiti 兼容格式"""
        # Graphiti 需要 Cypher 格式
        cypher_statements = []
        
        for traj in trajectories:
            # 创建 Episode 节点
            cypher_statements.append(
                f"CREATE (e:Episode {{id: '{traj.instance_id}', repo: '{traj.repo}'}})"
            )
            
            # 创建实体节点
            for entity in traj.entities:
                cypher_statements.append(
                    f"MERGE (n:{entity.entity_type} {{id: '{entity.entity_id}', name: '{entity.name}'}})"
                )
            
            # 创建关系
            for rel in traj.relations:
                cypher_statements.append(
                    f"MATCH (a {{id: '{rel.source_id}'}}), (b {{id: '{rel.target_id}'}}) "
                    f"CREATE (a)-[:{rel.relation_type}]->(b)"
                )
        
        with open(output_path, 'w') as f:
            f.write('\n'.join(cypher_statements))
    
    def export_for_amem(self, trajectories: List[Trajectory], output_path: Path):
        """导出为 A-MEM 兼容格式 (Note 结构)"""
        notes = []
        
        for traj in trajectories:
            # 每个轨迹作为一个 memory note
            note = {
                'note_id': traj.instance_id,
                'timestamp': datetime.now().isoformat(),
                'content': traj.problem_statement[:500],
                'context': f"Repo: {traj.repo}, Resolved: {traj.is_resolved}",
                'keywords': list(set(e.name for e in traj.entities[:10])),
                'tags': [traj.repo.split('/')[-1], 'resolved' if traj.is_resolved else 'unresolved'],
                
                # 链接信息
                'linked_entities': [e.to_dict() for e in traj.entities],
                'linked_relations': [r.to_dict() for r in traj.relations],
            }
            notes.append(note)
        
        with open(output_path, 'w') as f:
            json.dump(notes, f, indent=2, default=str)


# ============================================================================
# 主函数
# ============================================================================

def main():
    """主函数示例"""
    import argparse
    
    parser = argparse.ArgumentParser(description='SWE-bench Trajectory Analyzer')
    parser.add_argument('--input', '-i', type=str, help='Input trajectory file or directory')
    parser.add_argument('--output', '-o', type=str, default='output', help='Output directory')
    parser.add_argument('--format', '-f', choices=['json', 'graphiti', 'amem'], default='json')
    
    args = parser.parse_args()
    
    # 初始化
    parser = TrajectoryParser()
    analyzer = TrajectoryAnalyzer()
    exporter = GraphExporter()
    
    # 加载轨迹
    input_path = Path(args.input) if args.input else None
    
    if input_path and input_path.is_file():
        traj = parser.parse_swe_agent_trajectory(input_path)
        analyzer.add_trajectory(traj)
    elif input_path and input_path.is_dir():
        for traj_file in input_path.glob('*.traj'):
            traj = parser.parse_swe_agent_trajectory(traj_file)
            analyzer.add_trajectory(traj)
    
    # 分析
    stats = analyzer.compute_statistics()
    comparison = analyzer.compare_resolved_vs_unresolved()
    patterns = analyzer.extract_success_patterns()
    
    # 输出
    output_path = Path(args.output)
    output_path.mkdir(exist_ok=True)
    
    # 保存统计
    with open(output_path / 'statistics.json', 'w') as f:
        json.dump({
            'statistics': stats,
            'comparison': comparison,
            'patterns': patterns,
        }, f, indent=2, default=str)
    
    # 导出图数据
    if args.format == 'json':
        exporter.export_to_json(analyzer.trajectories, output_path / 'graph.json')
    elif args.format == 'graphiti':
        exporter.export_for_graphiti(analyzer.trajectories, output_path / 'graph.cypher')
    elif args.format == 'amem':
        exporter.export_for_amem(analyzer.trajectories, output_path / 'notes.json')
    
    print(f"Analysis complete. Results saved to {output_path}")
    print(f"Total trajectories: {stats.get('total_trajectories', 0)}")
    print(f"Resolution rate: {stats.get('resolution_rate', 0):.2%}")


if __name__ == '__main__':
    main()

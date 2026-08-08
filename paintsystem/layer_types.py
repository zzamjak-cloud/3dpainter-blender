"""레이어 타입 레지스트리.

새 레이어 타입을 추가할 때 수정해야 하는 지점을 한 곳으로 모은다.
bpy 에 의존하지 않으므로 어떤 모듈에서든 순환 import 없이 참조할 수 있다.

그래프 빌더 함수는 bpy 를 쓰는 graph.basic_layers 에 있으므로 여기서는
함수 이름(build_graph)만 들고 있고, 실제 dispatch 는 basic_layers 가 구성한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class LayerTypeSpec:
    id: str
    label: str
    description: str
    version: int
    # draw_layer_icon 이 쓰는 Blender 내장 아이콘 이름.
    # None 이면 draw_layer_icon 에 전용 그리기 분기가 있거나 기본 아이콘으로 떨어진다.
    icon: Optional[str] = None
    # coord_type(UV/DECAL 등) 프로퍼티를 실제로 사용하는 타입인지
    uses_coord: bool = False
    # 하위 레이어의 색 데이터를 변형하는 타입인지 (베이크 병합 가능 여부 판정에 쓰임)
    modifies_color: bool = False
    # 서브 타입에 따라 조건부로 색 데이터를 변형하는 경우의 판정자
    modifies_color_when: Optional[Callable[[Any], bool]] = None
    # 구버전 파일에서 source 노드가 쓰던 식별자
    legacy_source_name: Optional[str] = None
    # update_node_tree 의 empty object 정리에서 제외되는 타입인지
    keeps_empty_object: bool = False
    # graph.basic_layers 의 그래프 빌더 함수 이름
    build_graph: Optional[str] = None
    # LAYER_TYPE_ENUM(UI 드롭다운)에 노출되는지. 레거시 별칭은 False.
    in_enum: bool = True


def _is_gradient_map(layer: Any) -> bool:
    return layer.gradient_type == "GRADIENT_MAP"


# 등록 순서가 곧 LAYER_TYPE_ENUM 의 순서다. 순서를 바꾸면 UI 드롭다운이 바뀐다.
LAYER_TYPES: dict[str, LayerTypeSpec] = {
    spec.id: spec
    for spec in (
        LayerTypeSpec(
            id="FOLDER",
            label="Folder",
            description="Folder layer",
            version=4,
            build_graph="create_folder_graph",
        ),
        LayerTypeSpec(
            id="IMAGE",
            label="Image",
            description="Image layer",
            version=6,
            uses_coord=True,
            legacy_source_name="image",
            keeps_empty_object=True,
            build_graph="create_image_graph",
        ),
        LayerTypeSpec(
            id="SOLID_COLOR",
            label="Solid Color",
            description="Solid Color layer",
            version=4,
            legacy_source_name="rgb",
            build_graph="create_solid_graph",
        ),
        LayerTypeSpec(
            id="ATTRIBUTE",
            label="Attribute",
            description="Attribute layer",
            version=4,
            icon="MESH_DATA",
            modifies_color=True,
            legacy_source_name="attribute",
            build_graph="create_attribute_graph",
        ),
        LayerTypeSpec(
            id="ADJUSTMENT",
            label="Adjustment",
            description="Adjustment layer",
            version=4,
            icon="SHADERFX",
            legacy_source_name="adjustment",
            build_graph="create_adjustment_graph",
        ),
        LayerTypeSpec(
            id="NODE_GROUP",
            label="Node Group",
            description="Node Group layer",
            version=4,
            icon="NODETREE",
            legacy_source_name="custom_node_tree",
            build_graph="create_custom_graph",
        ),
        LayerTypeSpec(
            id="GRADIENT",
            label="Gradient",
            description="Gradient layer",
            version=4,
            # FAKE_LIGHT 그라디언트는 draw_layer_icon 에서 따로 처리한다.
            icon="COLOR",
            modifies_color_when=_is_gradient_map,
            legacy_source_name="gradient",
            keeps_empty_object=True,
            build_graph="create_gradient_graph",
        ),
        LayerTypeSpec(
            id="RANDOM",
            label="Random",
            description="Random Color layer",
            version=5,
            icon="SEQ_HISTOGRAM",
            build_graph="create_random_graph",
        ),
        LayerTypeSpec(
            id="TEXTURE",
            label="Texture",
            description="Texture layer",
            version=4,
            icon="TEXTURE",
            uses_coord=True,
            legacy_source_name="texture",
            keeps_empty_object=True,
            build_graph="create_texture_graph",
        ),
        LayerTypeSpec(
            id="GEOMETRY",
            label="Geometry",
            description="Geometry layer",
            version=4,
            icon="MESH_DATA",
            build_graph="create_geometry_graph",
        ),
        # BLANK 은 노드 트리를 만들지 않는 자리표시자 타입이다.
        LayerTypeSpec(
            id="BLANK",
            label="Blank",
            description="Blank layer",
            version=0,
        ),
        # 구버전 파일에만 남아 있는 별칭. NODE_GROUP 과 같은 버전을 써야 한다.
        LayerTypeSpec(
            id="CUSTOM",
            label="Node Group",
            description="Node Group layer",
            version=4,
            in_enum=False,
        ),
    )
}

# 알 수 없는 타입에 쓰는 기본 아이콘
DEFAULT_LAYER_ICON = "BLANK1"


def get_layer_type(type_id: str) -> Optional[LayerTypeSpec]:
    return LAYER_TYPES.get(type_id)


def build_layer_type_enum() -> list[tuple[str, str, str]]:
    """Blender EnumProperty 용 항목 목록. 등록 순서를 그대로 유지한다."""
    return [(s.id, s.label, s.description) for s in LAYER_TYPES.values() if s.in_enum]


def get_layer_version(type_id: str) -> int:
    spec = LAYER_TYPES.get(type_id)
    return spec.version if spec else 0


def layer_modifies_color(layer: Any) -> bool:
    """레이어 타입(및 서브 타입)이 하위 색 데이터를 변형하는지."""
    spec = LAYER_TYPES.get(layer.type)
    if not spec:
        return False
    if spec.modifies_color:
        return True
    return bool(spec.modifies_color_when and spec.modifies_color_when(layer))

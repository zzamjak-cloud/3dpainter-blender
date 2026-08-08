"""채널 노드 그래프 빌드 (Channel.update_node_tree 본체)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, Optional

from bpy.types import Context, Material

from .basic_layers import get_alpha_over_nodetree
from .common import get_library_nodetree
from .nodetree_builder import Add_Node, NodeTreeBuilder

if TYPE_CHECKING:
    from ..data import Channel


def vector_transform(
    node_builder: NodeTreeBuilder,
    color_name: str,
    color_socket: str,
    convert_from: str,
    convert_to: str,
    normalize_input: bool,
    normalize_output: bool,
    vector_type: str,
    tangent_uv: str = "UVMap",
):
    """Transform the vector output of the channel to the output vector space.

    Args:
        node_builder (NodeTreeBuilder): The node tree builder to add the nodes to.
        color_name (str): The name of the color socket to transform.
        color_socket (str): The socket of the color socket to transform.
        convert_from (str): The input vector space. Can be "WORLD", "OBJECT", "TANGENT".
        convert_to (str): The output vector space. Can be "WORLD", "OBJECT", "TANGENT".
        normalize_input (bool): Whether to normalize the input vector.
        normalize_output (bool): Whether to normalize the output vector.
        vector_type (str): The type of the vector. Can be "POINT", "VECTOR", "NORMAL".
        tangent_uv (str): The UV map to use for the tangent space.

    Returns:
        tuple[str, str]: The name and socket of the transformed vector.
    """
    output_name = None
    output_socket = None
    new_color_name = None
    new_color_socket = None
    is_output_tangent = convert_to == "TANGENT"
    if convert_from == "TANGENT":
        # Tangent space is already normalized
        normalize_input = True
    if is_output_tangent:
        # Do not need to normalize output as the conversion from tangent space to tangent space is already normalized
        normalize_output = True
        # need to set convert_to to WORLD first before conversion
        convert_to = "WORLD"
    if normalize_input:
        normal_map_name = node_builder.get_unique_identifier("normal_map")
        node_builder.add_node(normal_map_name, "ShaderNodeNormalMap", {"space": convert_from, 'uv_map': tangent_uv}, force_properties=True)
        if not output_name and not output_socket:
            new_color_name = normal_map_name
            new_color_socket = "Color"
        else:
            node_builder.link(output_name, normal_map_name, output_socket, "Normal")
        output_name = normal_map_name
        output_socket = "Normal"
        convert_from = "WORLD"
    if convert_from != convert_to:
        vector_transform_name = node_builder.get_unique_identifier("vector_transform_output")
        node_builder.add_node(vector_transform_name, "ShaderNodeVectorTransform", {"vector_type": vector_type, "convert_from": convert_from, "convert_to": convert_to}, force_properties=True)
        if not output_name and not output_socket:
            new_color_name = vector_transform_name
            new_color_socket = "Vector"
        else:
            node_builder.link(output_name, vector_transform_name, output_socket, "Vector")
        output_name = vector_transform_name
        output_socket = "Vector"
    if normalize_output:
        if is_output_tangent:
            tangent_norm_nt = get_library_nodetree(".PS Tangent Normal")
            tangent_norm_name = node_builder.get_unique_identifier("tangent_normalize")
            node_builder.add_node("tangent", "ShaderNodeTangent", {"direction_type": "UV_MAP", "uv_map": tangent_uv})
            node_builder.add_node(tangent_norm_name, "ShaderNodeGroup", {"node_tree": tangent_norm_nt}, force_properties=True)
            node_builder.link("tangent", tangent_norm_name, "Tangent", "Tangent")
            if not output_name and not output_socket:
                new_color_name = tangent_norm_name
                new_color_socket = "Custom Normal"
            else:
                node_builder.link(output_name, tangent_norm_name, output_socket, "Custom Normal")
            output_name = tangent_norm_name
            output_socket = "Tangent Normal"
        else:
            normalize_name = node_builder.get_unique_identifier("normalize")
            node_builder.add_node(normalize_name, "ShaderNodeVectorMath", {"operation": "MULTIPLY_ADD", "hide": True}, {1: (0.5, 0.5, 0.5), 2: (0.5, 0.5, 0.5)})
            if not output_name and not output_socket:
                new_color_name = normalize_name
                new_color_socket = "Vector"
            else:
                node_builder.link(output_name, normalize_name, output_socket, "Vector")
            output_name = normalize_name
            output_socket = "Vector"
    if output_name and output_socket:
        node_builder.link(output_name, color_name, output_socket, color_socket)
    else:
        new_color_name = color_name
        new_color_socket = color_socket
    return new_color_name, new_color_socket


def build_channel_graph(channel: "Channel", context: Context):
    """Channel.update_node_tree 본체를 채널 인스턴스 기준으로 빌드한다."""
    # 순환 import 회피: data 로드 이후에만 캐시 무효화 헬퍼를 가져온다
    from ..data import _invalidate_material_layer_cache

    if not channel.node_tree:
        return
    # 레이어가 바뀐 것은 이 채널을 소유한 머티리얼이다. 활성 머티리얼과 다를 수 있으므로 소유자를 무효화한다
    owner_material = channel.id_data
    _invalidate_material_layer_cache(owner_material if isinstance(owner_material, Material) else None)

    channel.node_tree.name = f".PS {channel.name}"
    if len(channel.node_tree.interface.items_tree) == 0:
        channel.node_tree.interface.new_socket("Color", in_out="OUTPUT", socket_type="NodeSocketColor")
        channel.node_tree.interface.new_socket("Alpha", in_out="OUTPUT", socket_type="NodeSocketFloat")
        channel.node_tree.interface.new_socket("Color", in_out="INPUT", socket_type="NodeSocketColor")
        channel.node_tree.interface.new_socket("Alpha", in_out="INPUT", socket_type="NodeSocketFloat")
    node_builder = NodeTreeBuilder(channel.node_tree, frame_name="Channel Graph", node_width=200)
    node_builder.add_node("group_input", "NodeGroupInput")
    node_builder.add_node("group_output", "NodeGroupOutput")

    flattened_unlinked_layers = channel.flattened_unlinked_layers

    @dataclass
    class PreviousLayer:
        color_name: str
        color_socket: str
        alpha_name: str
        alpha_socket: str
        clip_mode = False
        add_command: Optional[Add_Node] = None
        clip_color_name: Optional[str] = None
        clip_alpha_name: Optional[str] = None
        clip_color_socket: Optional[str] = None
        clip_alpha_socket: Optional[str] = None
        passthrough_id: Optional[int] = None

    def connect_passthrough(node_builder: NodeTreeBuilder, layer_identifier: str, previous_data: PreviousLayer):
        if previous_data.passthrough_id:
            passthrough_data = previous_dict.get(previous_data.passthrough_id, None)
            if passthrough_data:
                node_builder.link(layer_identifier,
                                passthrough_data.color_name,
                                "Color",
                                passthrough_data.color_socket)
                node_builder.link(layer_identifier,
                                passthrough_data.alpha_name,
                                "Alpha", passthrough_data.alpha_socket)
                previous_data.passthrough_id = None

    previous_dict: Dict[int, PreviousLayer] = {}

    node_builder.add_node("alpha_clamp_end", "ShaderNodeClamp", {"hide": True})
    node_builder.link("alpha_clamp_end", "group_output", "Result", "Alpha")
    previous_dict[-1] = PreviousLayer(color_name="group_output", color_socket="Color", alpha_name="alpha_clamp_end", alpha_socket="Value")

    previous_data = previous_dict.get(-1)
    if channel.type == "VECTOR" and channel.use_space_transform_output and not channel.disable_output_transform:
        color_name, color_socket = vector_transform(
            node_builder,
            previous_data.color_name,
            previous_data.color_socket,
            channel.bake_vector_space if channel.use_bake_image else channel.vector_space,
            channel.output_vector_space,
            channel.normalize_input,
            False,
            channel.vector_type,
            channel.bake_uv_map if channel.use_bake_image else channel.tangent_uv_map
        )
        previous_data.color_name = color_name
        previous_data.color_socket = color_socket

    if channel.bake_image:
        node_builder.add_node("uv_map", "ShaderNodeUVMap", {"uv_map": channel.bake_uv_map}, force_properties=True)
        node_builder.add_node("bake_image", "ShaderNodeTexImage", {"image": channel.bake_image, "interpolation": "Closest"})
        node_builder.link("uv_map", "bake_image", "UV", "Vector")
        if channel.use_bake_image:
            node_builder.link("bake_image", previous_data.color_name, "Color", previous_data.color_socket)
            node_builder.link("bake_image", "group_output", "Alpha", "Alpha")
            node_builder.compile()
            return

    if len(flattened_unlinked_layers) > 0:
        for unlinked_layer in flattened_unlinked_layers:
            layer = unlinked_layer.get_layer_data()
            if layer is None or not layer.node_tree:
                continue
            sample_id = unlinked_layer.parent_id
            if unlinked_layer.parent_id != -1:
                sample_id = channel.get_parent_layer_id(unlinked_layer)
            previous_data = previous_dict.get(sample_id, None)
            layer_identifier = unlinked_layer.uid
            add_command = node_builder.add_node(
                layer_identifier, "ShaderNodeGroup",
                {"node_tree": layer.node_tree},
                {"Clip": layer.is_clip or layer.type == "ADJUSTMENT"},
                force_properties=True,
                force_default_values=True
            )
            previous_data.add_command = add_command
            if layer.is_clip and not previous_data.clip_mode:
                previous_data.clip_mode = True
                clip_nt = get_alpha_over_nodetree()
                clip_nt_identifier = f"clip_nt_{layer.id}"
                node_builder.add_node(clip_nt_identifier, "ShaderNodeGroup", {"node_tree": clip_nt}, {"Color": (0, 0, 0, 1), "Alpha": 0}, force_default_values=True)
                node_builder.link(clip_nt_identifier, previous_data.color_name, "Color", previous_data.color_socket)
                node_builder.link(clip_nt_identifier, previous_data.alpha_name, "Alpha", previous_data.alpha_socket)
                connect_passthrough(node_builder, clip_nt_identifier, previous_data)
                previous_data.color_name = clip_nt_identifier
                previous_data.color_socket = "Color"
                previous_data.alpha_name = clip_nt_identifier
                previous_data.alpha_socket = "Alpha"
                previous_data.clip_color_name = clip_nt_identifier
                previous_data.clip_color_socket = "Over Color"
                previous_data.clip_alpha_name = clip_nt_identifier
                previous_data.clip_alpha_socket = "Over Alpha"
            target_color = previous_data.clip_color_name if previous_data.clip_mode else previous_data.color_name
            target_color_socket = previous_data.clip_color_socket if previous_data.clip_mode else previous_data.color_socket
            target_alpha = previous_data.clip_alpha_name if previous_data.clip_mode else previous_data.alpha_name
            target_alpha_socket = previous_data.clip_alpha_socket if previous_data.clip_mode else previous_data.alpha_socket
            node_builder.link(layer_identifier,
                            target_color,
                            "Color",
                            target_color_socket)
            node_builder.link(layer_identifier,
                            target_alpha,
                            "Alpha",
                            target_alpha_socket)
            connect_passthrough(node_builder, layer_identifier, previous_data)
            if layer.blend_mode == "PASSTHROUGH":
                previous_data.passthrough_id = unlinked_layer.id
            if previous_data.clip_mode:
                previous_data.clip_color_name = layer_identifier
                previous_data.clip_color_socket = "Color"
                previous_data.clip_alpha_name = layer_identifier
                previous_data.clip_alpha_socket = "Alpha"
            else:
                previous_data.color_name = layer_identifier
                previous_data.color_socket = "Color"
                previous_data.alpha_name = layer_identifier
                previous_data.alpha_socket = "Alpha"
            if layer.type == "FOLDER":
                previous_dict[unlinked_layer.id] = PreviousLayer(
                    color_name=layer_identifier,
                    color_socket="Over Color",
                    alpha_name=layer_identifier,
                    alpha_socket="Over Alpha"
                )
            if previous_data.clip_mode and not layer.is_clip:
                previous_data.clip_mode = False
    prev_layer = previous_dict[-1]
    if channel.type == "VECTOR" and channel.use_space_transform_input:
        color_name, color_socket = vector_transform(
            node_builder,
            prev_layer.color_name,
            prev_layer.color_socket,
            channel.input_vector_space,
            channel.vector_space,
            False,
            channel.normalize_input,
            channel.vector_type,
            channel.tangent_uv_map
        )
        prev_layer.color_name = color_name
        prev_layer.color_socket = color_socket
        if channel.default_value != "NONE":
            node_builder.add_node("vector_length", "ShaderNodeVectorMath", {"operation": "LENGTH"})
            node_builder.add_node("compare", "ShaderNodeMath", {"operation": "COMPARE"}, {1: 0, 2: 0})
            node_builder.add_node("vector_mix", "ShaderNodeMix", {"data_type": "VECTOR"})
            node_builder.link("group_input", "vector_length", "Color", "Vector")
            node_builder.link("vector_length", "compare", "Value", "Value")
            node_builder.link("compare", "vector_mix", "Value", "Factor")
            node_builder.link("vector_mix", prev_layer.color_name, "Result", prev_layer.color_socket)
            match channel.default_value:
                case "NORMAL":
                    node_builder.add_node("geometry", "ShaderNodeNewGeometry")
                    node_builder.link("geometry", "vector_mix", "Normal", "B")
                case "WORLD_POSITION":
                    node_builder.add_node("geometry", "ShaderNodeNewGeometry")
                    node_builder.link("geometry", "vector_mix", "Position", "B")
                case "OBJECT_POSITION":
                    node_builder.add_node("geometry", "ShaderNodeTexCoord")
                    node_builder.link("geometry", "vector_mix", "Object", "B")
            prev_layer.color_name = "vector_mix"
            prev_layer.color_socket = "A"
    node_builder.link("group_input", prev_layer.color_name, "Color", prev_layer.color_socket)
    node_builder.add_node("alpha_clamp_start", "ShaderNodeClamp", {"hide": True})
    node_builder.link("alpha_clamp_start", prev_layer.alpha_name, "Result", prev_layer.alpha_socket)
    node_builder.link("group_input", "alpha_clamp_start", "Alpha", "Value")
    node_builder.compile()

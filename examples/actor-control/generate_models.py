"""Reproduce two embedded-weight policies: [position, 1] @ [0, direction]."""

from pathlib import Path

import onnx
from onnx import TensorProto, helper

directory = Path(__file__).with_name("models")
directory.mkdir(exist_ok=True)
for name, direction in (("right", 1.0), ("left", -1.0)):
    graph = helper.make_graph(
        [helper.make_node("MatMul", ["observation", "weights"], ["action"])],
        name,
        [helper.make_tensor_value_info("observation", TensorProto.FLOAT, [1, 2])],
        [helper.make_tensor_value_info("action", TensorProto.FLOAT, [1, 1])],
        [helper.make_tensor("weights", TensorProto.FLOAT, [2, 1], [0.0, direction])],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)], ir_version=8)
    onnx.checker.check_model(model)
    onnx.save_model(model, directory / f"{name}.onnx")

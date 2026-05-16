#!/usr/bin/env python3
"""Generate gRPC Python stubs from .proto files."""
import sys
import os
import grpc_tools
from grpc_tools import protoc

well_known = os.path.join(os.path.dirname(grpc_tools.__file__), "_proto")
proto_dir = sys.argv[1]
out_dir = sys.argv[2]
protos = sys.argv[3:]

os.makedirs(out_dir, exist_ok=True)

rc = protoc.main(
    ["protoc", f"-I{well_known}", f"-I{proto_dir}",
     f"--python_out={out_dir}", f"--grpc_python_out={out_dir}"]
    + protos
)

# Write __init__.py so the directory is a package
init = os.path.join(out_dir, "__init__.py")
if not os.path.exists(init):
    open(init, "w").close()

sys.exit(rc)

"""Compiled GTFS-Realtime + TfNSW field-1007 extension bindings.

gtfs_realtime_1007_extension_pb2.py is generated from
gtfs-realtime_1007_extension.proto (fetched from the Open Data Hub's
vehicle-positions-v2 dataset) via:

    python -m grpc_tools.protoc -I engine/normalise/proto \\
        --python_out=engine/normalise/proto \\
        engine/normalise/proto/gtfs-realtime_1007_extension.proto

Re-run that if the .proto file is ever refreshed from the Open Data Hub.
It is compiled standalone rather than via the gtfs-realtime-bindings package
because the TfNSW extension file redefines the base messages inline instead of
importing Google's official proto.
"""
from . import gtfs_realtime_1007_extension_pb2 as pb

__all__ = ["pb"]

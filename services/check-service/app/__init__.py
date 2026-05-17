import os
import sys


_proto_gen = os.path.abspath(os.path.join(os.path.dirname(__file__), "proto_gen"))
if _proto_gen not in sys.path:
    sys.path.insert(0, _proto_gen)

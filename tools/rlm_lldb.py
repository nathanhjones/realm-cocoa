##############################################################################
#
# Copyright 2014 Realm Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http:#www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
##############################################################################

import lldb

# command script import /src/rlm_lldb.py --allow-reload

property_types = {
    0: 'int64_t',
    10: 'double',
    1: 'bool',
    9: 'float',
}

def cache_lookup(cache, key, generator):
    value = cache.get(key, None)
    if not value:
        value = generator(key)
        cache[key] = value
    return value

ivar_cache = {}
def get_ivar(obj, addr, ivar):
    def get_offset(ivar):
        class_name, ivar_name = ivar.split('.')
        frame = obj.GetThread().GetSelectedFrame()
        ptr = frame.EvaluateExpression("&(({} *)0)->_{}".format(class_name, ivar_name))
        return (ptr.GetValueAsUnsigned(), ptr.deref.size)

    offset, size = cache_lookup(ivar_cache, ivar, get_offset)
    return obj.GetProcess().ReadUnsignedFromMemory(int(addr) + offset, size, lldb.SBError())

class SyntheticChildrenProvider(object):
    def _eval(self, expr):
        frame = self.obj.GetThread().GetSelectedFrame()
        return frame.EvaluateExpression(expr)

    def _get_ivar(self, addr, ivar):
        return get_ivar(self.obj, addr, ivar)

    def _to_str(self, val):
        return self.obj.GetProcess().ReadCStringFromMemory(val, 1024, lldb.SBError())

schema_cache = {}
class RLMObject_SyntheticChildrenProvider(SyntheticChildrenProvider):
    def __init__(self, obj, _):
        self.obj = obj
        objectSchema = self._get_ivar(self.obj.GetAddress(), 'RLMObject.objectSchema')

        def get_schema(objectSchema):
            properties = self._get_ivar(objectSchema, 'RLMObjectSchema.properties')
            count = self._eval("(NSUInteger)[((NSArray *){}) count]".format(properties)).GetValueAsUnsigned()
            return [self._get_prop(properties, i) for i in range(count)]

        self.props = cache_lookup(schema_cache, objectSchema, get_schema)

    def num_children(self):
        return len(self.props)

    def has_children(self):
        return True

    def get_child_index(self, name):
        return next(i for i, (prop_name, _) in enumerate(self.props) if prop_name == name)

    def get_child_at_index(self, index):
        name, getter = self.props[index]
        value = self._eval(getter)
        return self.obj.CreateValueFromData(name, value.GetData(), value.GetType())

    def update(self):
        pass

    def _get_prop(self, props, i):
        prop = self._eval("(NSUInteger)[((NSArray *){}) objectAtIndex:{}]".format(props, i)).GetValueAsUnsigned()
        name = self._to_str(self._eval('[(NSString *){} UTF8String]'.format(self._get_ivar(prop, "RLMProperty.name"))).GetValueAsUnsigned())
        type = self._get_ivar(prop, 'RLMProperty.type')
        getter = "({})[(id){} {}]".format(property_types.get(type, 'id'), self.obj.GetAddress(), name)
        return name, getter

class_name_cache = {}
def RLMArray_SummaryProvider(obj, _):
    frame = obj.GetThread().GetSelectedFrame()

    class_name_ptr = get_ivar(obj, obj.GetAddress(), 'RLMArray.objectClassName')
    def get_class_name(ptr):
        utf8_addr = frame.EvaluateExpression('(const char *)[(NSString *){} UTF8String]'.format(class_name_ptr)).GetValueAsUnsigned()
        return obj.GetProcess().ReadCStringFromMemory(utf8_addr, 1024, lldb.SBError())

    class_name = cache_lookup(class_name_cache, class_name_ptr, get_class_name)
    count = frame.EvaluateExpression('(NSUInteger)[(RLMArray *){} count]'.format(obj.GetAddress())).GetValueAsUnsigned()
    return "({}[{}])".format(class_name, count)

class RLMArray_SyntheticChildrenProvider(SyntheticChildrenProvider):
    def __init__(self, valobj, _):
        self.obj = valobj
        self.addr = self.obj.GetAddress()

    def num_children(self):
        return self.count

    def has_children(self):
        return True

    def get_child_index(self, name):
        return int(name.lstrip('[').rstrip(']'))

    def get_child_at_index(self, index):
        value = self._eval('(id)[(id){} objectAtIndex:{}]'.format(self.addr, index))
        return self.obj.CreateValueFromData('[' + str(index) + ']', value.GetData(), value.GetType())

    def update(self):
        self.count = self._eval("(NSUInteger)[(RLMArray *){} count]".format(self.addr)).GetValueAsUnsigned()

def __lldb_init_module(debugger, _):
    debugger.HandleCommand('type summary add RLMArray -F rlm_lldb.RLMArray_SummaryProvider')
    debugger.HandleCommand('type summary add RLMArrayLinkView -F rlm_lldb.RLMArray_SummaryProvider')
    debugger.HandleCommand('type summary add RLMArrayTableView -F rlm_lldb.RLMArray_SummaryProvider')

    debugger.HandleCommand('type synthetic add RLMArray --python-class rlm_lldb.RLMArray_SyntheticChildrenProvider')
    debugger.HandleCommand('type synthetic add RLMArrayLinkView --python-class rlm_lldb.RLMArray_SyntheticChildrenProvider')
    debugger.HandleCommand('type synthetic add RLMArrayTableView --python-class rlm_lldb.RLMArray_SyntheticChildrenProvider')
    debugger.HandleCommand('type synthetic add -x RLMAccessor_.* --python-class rlm_lldb.RLMObject_SyntheticChildrenProvider')

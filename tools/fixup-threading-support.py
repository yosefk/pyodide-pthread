'''
patches the emscripten-produced pyodide.asm.js to support spawning pthreads
'''

def insert_after(lines, after_what, insert_what):
    for i,line in enumerate(lines):
        if after_what == line.strip():
            lines.insert(i+1, insert_what)
            return
    assert False, 'failed to insert `{insert_what}` after `{after_what}` - did not find the latter'

def add_sentinel_dummies(lines):
    '''is_sentinel and create_sentinel won't resolve in the pthread; we want them to resolve quietly to
    'whatever' - currently threads are just for self-contained C code that won't call these or anything
    else outside C libraries'''

    insert_after(lines, '"env": wasmImports,', '''
    ...(ENVIRONMENT_IS_PTHREAD && { "sentinel": {"is_sentinel":_is_sentinel, "create_sentinel":_create_sentinel} }), //stubs-won't actually work, but the linking will pass
''')

def add_pthread_main_js(lines):
    '''since Pyodide currently doesn't seem to provide a way to set Module["mainScriptUrlOrBlob"], we add code
    getting the location of pyodide.asm.js from a global variable PTHREAD_MAIN_JS which can be set in the
    worker before spawning the pthread; failing that, the pthread will try loading the worker js file instead
    of pyodide.asm.js, and fail'''

    insert_after(lines, 'var pthreadMainJs = _scriptName;', '''
    if(typeof PTHREAD_MAIN_JS !== 'undefined') {
        pthreadMainJs = PTHREAD_MAIN_JS;
    }
''')

def add_dyn_lib_offsets(lines):
    '''this fixes a problem that seems to exist regardless of Pyodide, but is perhaps somewhat Pyodide-specific
    in that Pyodide might be adding entries to wasmTable which many modules presumably don't do.

    emscripten generates code which loads all the shared objects in the newly spawned pthread in the order
    in which they were loaded in the parent thread. this fails if the parent thread added entries to wasmTable
    between these modules; you'll get different offsets in the parent and child thread, and when you pass function
    pointers (such as the thread entry point), the wrong function will get called.

    to fix this, we pass the list of offsets to which the libraries where loaded in the parent thread, and grow
    the table to the size matching the offset of each library in the parent before loading it in the child.
    '''
   
    insert_after(lines, 'var moduleRtn;', '''
  var myDynamicLibraryOffsets = {};
  var parentDynamicLibraryOffsets = {};
''')

    insert_after(lines, 'dynamicLibraries = msgData.dynamicLibraries;', '''
        //we use this to make sure dynamic libraries are loaded to the same wasmTable offset in this thread
        //as they are in the parent (this is not obviously going to happen since eg getEmptyTableSlot() grows the table
        //and could have been called in the parent in between dlopen/other module loading calls)
        if(msgData.dynamicLibraryOffsets !== undefined) {
          parentDynamicLibraryOffsets = msgData.dynamicLibraryOffsets;
        }
''')

    insert_after(lines, 'dynamicLibraries,', '''
      dynamicLibraryOffsets: myDynamicLibraryOffsets,
''')

    insert_after(lines, '}, localScope, handle) {', '''
  if(ENVIRONMENT_IS_PTHREAD && parentDynamicLibraryOffsets !== undefined) {
    const offset = parentDynamicLibraryOffsets[libName];
    //offset<wasmTable.length means that we can't load the library to the right offset, so we give up.
    //note that it will fail, in practice, when a thread finishes and is reused since the library
    //is already loaded into that thread and wasmTable.length will be too high.
    //we don't really want to "properly" support this ATM though it's doable and would require to keep track
    //of the offsets of previously loaded libraries.
    if(offset === undefined || offset < wasmTable.length) {
      throw new Error(`failing to load dynamic library ${libName} - parent offset is ${offset}, wasmTable.length=${wasmTable.length} (NOTE: if you joined a thread and created a new one, it might have caused this error)`);
    }
    //make sure we load the dynamic library to the same offset as it was loaded to in the parent -
    //otherwise function pointers (indexes) will not match in the child & parent thread
    if(offset > wasmTable.length) {
      wasmTable.grow(offset - wasmTable.length);
    }
  }

  myDynamicLibraryOffsets[libName] = wasmTable.length;
''')

import sys

asm_js_file = sys.argv[1]
lines=open(asm_js_file).read().split('\n')

add_sentinel_dummies(lines)
add_pthread_main_js(lines)
add_dyn_lib_offsets(lines)

with open(asm_js_file,'w') as f:
    f.write('\n'.join(lines))
    f.write('\n')

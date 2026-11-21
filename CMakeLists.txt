cmake_minimum_required(VERSION 3.22)
project(steve_engine_native LANGUAGES C CXX)

include(FetchContent)

set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
set(BOX2D_SAMPLES OFF CACHE BOOL "" FORCE)
set(BOX2D_BENCHMARKS OFF CACHE BOOL "" FORCE)
set(BOX2D_DOCS OFF CACHE BOOL "" FORCE)
set(BOX2D_UNIT_TESTS OFF CACHE BOOL "" FORCE)

FetchContent_Declare(
    box2d
    GIT_REPOSITORY https://github.com/erincatto/box2d.git
    GIT_TAG v3.2.0
    GIT_SHALLOW TRUE
)
FetchContent_MakeAvailable(box2d)

add_library(engine_physics_native SHARED engine_editor/engine_native.cpp)
target_compile_features(engine_physics_native PRIVATE cxx_std_17)
target_link_libraries(engine_physics_native PRIVATE box2d::box2d)
set_target_properties(engine_physics_native PROPERTIES
    PREFIX ""
    OUTPUT_NAME "engine_physics_native"
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/engine_editor"
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/engine_editor"
    RUNTIME_OUTPUT_DIRECTORY_RELEASE "${CMAKE_CURRENT_SOURCE_DIR}/engine_editor"
    LIBRARY_OUTPUT_DIRECTORY_RELEASE "${CMAKE_CURRENT_SOURCE_DIR}/engine_editor"
)

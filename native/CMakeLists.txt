cmake_minimum_required(VERSION 3.24...3.31)
project(sculpt_kit_remesh LANGUAGES CXX C)

# Sculpt Kit's native remesh engine: the Instant Meshes library (BSD-3) bound in memory through nanobind.
# Built by scikit-build-core from pyproject.toml; run `pip wheel native/` or cibuildwheel, not cmake directly.
if (NOT SKBUILD)
  message(WARNING "This CMake file is meant to be driven by scikit-build-core (pip wheel native/).")
endif()

set(CMAKE_CXX_STANDARD 14)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
if (NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "" FORCE)
endif()

find_package(Python 3.12 COMPONENTS Interpreter Development.Module OPTIONAL_COMPONENTS Development.SABIModule REQUIRED)
find_package(nanobind CONFIG REQUIRED)

# ---------------------------------------------------------------------- sources fetched at configure time
include(FetchContent)
set(FETCHCONTENT_QUIET OFF)

# Instant Meshes at its last commit (Nov 2019). Only the library sources are compiled; the viewer (nanogui)
# is left out, and its submodules are limited to the ones the library needs.
FetchContent_Declare(instant_meshes
  GIT_REPOSITORY https://github.com/wjakob/instant-meshes.git
  GIT_TAG        7b3160864a2e1025af498c84cfed91cbfb613698
  GIT_SUBMODULES "ext/tbb" "ext/dset" "ext/pss" "ext/pcg32"
  GIT_SHALLOW    OFF
  # Our changes to the library live in patches/ (see each file's header); applied once, after the clone.
  PATCH_COMMAND  git apply --ignore-whitespace ${CMAKE_CURRENT_SOURCE_DIR}/patches/0001-cap-extraction-step5.patch
                                                ${CMAKE_CURRENT_SOURCE_DIR}/patches/0002-per-vertex-scale.patch
  UPDATE_DISCONNECTED ON
  SOURCE_SUBDIR  cmake-not-used)   # its own CMakeLists builds the viewer and wants nanogui: never entered
# Eigen 3.4 (MPL-2, header-only). Instant Meshes expects the copy inside nanogui, which is not fetched.
FetchContent_Declare(eigen
  URL      https://gitlab.com/libeigen/eigen/-/archive/3.4.0/eigen-3.4.0.tar.gz
  URL_HASH SHA256=8586084f71f9bde545ee7fa6d00288b264a2b7ac3607b974e54d13e7162c1c72
  DOWNLOAD_EXTRACT_TIMESTAMP ON
  SOURCE_SUBDIR  cmake-not-used)
FetchContent_MakeAvailable(instant_meshes eigen)

# ---------------------------------------------------------------------- TBB (the old fork Instant Meshes pins)
set(TBB_BUILD_STATIC ON CACHE BOOL "" FORCE)
set(TBB_BUILD_SHARED OFF CACHE BOOL "" FORCE)
set(TBB_BUILD_TBBMALLOC OFF CACHE BOOL "" FORCE)
set(TBB_BUILD_TBBMALLOC_PROXY OFF CACHE BOOL "" FORCE)
set(TBB_BUILD_TESTS OFF CACHE BOOL "" FORCE)
if (MSVC)
  add_definitions(/D_CRT_SECURE_NO_WARNINGS /D__TBB_NO_IMPLICIT_LINKAGE)
elseif (CMAKE_CXX_COMPILER_ID MATCHES "GNU" AND CMAKE_CXX_COMPILER_VERSION VERSION_GREATER_EQUAL "11.0")
  add_compile_options(-fpermissive)  # the 2017 TBB fork against a new libstdc++
endif()
add_subdirectory(${instant_meshes_SOURCE_DIR}/ext/tbb EXCLUDE_FROM_ALL)

# ---------------------------------------------------------------------- the module
set(IM ${instant_meshes_SOURCE_DIR}/src)
set(IM_SOURCES
  ${IM}/normal.cpp ${IM}/adjacency.cpp ${IM}/meshstats.cpp ${IM}/hierarchy.cpp ${IM}/extract.cpp
  ${IM}/field.cpp ${IM}/bvh.cpp ${IM}/subdivide.cpp ${IM}/reorder.cpp ${IM}/smoothcurve.cpp
  ${IM}/cleanup.cpp ${IM}/dedge.cpp ${IM}/meshio.cpp ${IM}/serializer.cpp
  ${instant_meshes_SOURCE_DIR}/ext/rply/rply.c)

nanobind_add_module(_engine STABLE_ABI NB_STATIC src/engine.cpp ${IM_SOURCES})
target_include_directories(_engine PRIVATE
  ${IM} ${eigen_SOURCE_DIR}
  ${instant_meshes_SOURCE_DIR}/ext/tbb/include ${instant_meshes_SOURCE_DIR}/ext/dset
  ${instant_meshes_SOURCE_DIR}/ext/pss ${instant_meshes_SOURCE_DIR}/ext/pcg32
  ${instant_meshes_SOURCE_DIR}/ext/rply ${instant_meshes_SOURCE_DIR}/ext/half)
target_link_libraries(_engine PRIVATE tbb_static)
# nanobind compiles the module as C++17; the 2017 TBB headers still use std::binary_function, removed in
# C++17 by libc++ (Apple) and MSVC unless these switches bring it back. GCC's libstdc++ only warns.
target_compile_definitions(_engine PRIVATE
  _LIBCPP_ENABLE_CXX17_REMOVED_UNARY_BINARY_FUNCTION _HAS_AUTO_PTR_ETC=1 _SILENCE_ALL_CXX17_DEPRECATION_WARNINGS)
if (MSVC)
  # Instant Meshes uses M_PI, which MSVC's <cmath> only defines on request; NOMINMAX keeps std::min/max usable.
  target_compile_definitions(_engine PRIVATE _USE_MATH_DEFINES NOMINMAX)
else()
  target_compile_options(_engine PRIVATE -Wno-deprecated-declarations -Wno-unused-result)
endif()

install(TARGETS _engine LIBRARY DESTINATION sculpt_kit_remesh)

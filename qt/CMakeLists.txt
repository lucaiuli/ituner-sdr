cmake_minimum_required(VERSION 3.24)

project(ituner-sdr-qt
    VERSION 0.1.0
    DESCRIPTION "iTuner SDR receiver interface in C++20 and Qt Quick"
    LANGUAGES CXX
)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE RelWithDebInfo CACHE STRING "Build type" FORCE)
endif()

option(ITUNER_QT_BUILD_TESTS "Build the Qt test suites" ON)

# 6.8 is the version available for arm64 in Raspberry Pi OS Trixie. Do not mix a
# second Qt from another source on the target.
find_package(Qt6 6.5 REQUIRED COMPONENTS Core Gui Qml Quick QuickControls2)

qt_standard_project_setup(REQUIRES 6.5)

# With QML files below the target directory, one qmldir per directory is the
# intended behaviour from Qt 6.8 on. `qt_policy` exists from Qt 6.5.
if(COMMAND qt_policy)
    qt_policy(SET QTP0004 NEW)
endif()

# The domain core first: it is the only part that must build without a GUI.
add_subdirectory(src/core)

qt_add_executable(ituner-sdr-qt
    src/app/main.cpp
    src/app/cli.cpp
    src/app/cli.h
    src/app/runtime_info.cpp
    src/app/runtime_info.h
    src/app/runtime_platform.cpp
    src/app/runtime_platform.h
    src/app/visual_self_test.cpp
    src/app/visual_self_test.h
)

qt_add_qml_module(ituner-sdr-qt
    URI ItunerSdr
    VERSION 0.1
    QML_FILES
        qml/Main.qml
        qml/OrientationCanvas.qml
        qml/TestPattern.qml
        qml/CornerMarker.qml
        qml/EdgeLabel.qml
        qml/FpsOverlay.qml
)

target_link_libraries(ituner-sdr-qt
    PRIVATE
        ituner_core
        Qt6::Core
        Qt6::Gui
        Qt6::Qml
        Qt6::Quick
        Qt6::QuickControls2
)

if(NOT MSVC)
    target_compile_options(ituner-sdr-qt PRIVATE -Wall -Wextra)
endif()

if(ITUNER_QT_BUILD_TESTS)
    enable_testing()
    add_subdirectory(tests)
endif()

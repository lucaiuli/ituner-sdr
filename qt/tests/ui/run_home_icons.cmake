# Render the Home screen twice -- with the installed rail icons and without --
# and check that the icons were loaded and drawn.
#
# The directory is resolved by the runtime, so there are two failure modes to
# catch: a path the QML cannot open (which it reports on stderr) and a path that
# opens but never reaches the frame (the two renders would then be identical).
#
# Run through `ctest`. The pixel comparison needs the application runtime
# (Pillow), so it reports a skip rather than a failure when the virtual
# environment is absent, and a missing artwork directory skips the same way.

if(NOT EXISTS "${PYTHON}")
    message(STATUS "skipping the Home icon check: ${PYTHON} is not present")
    return()
endif()

if(NOT EXISTS "${ICONS}")
    message(STATUS "skipping the Home icon check: ${ICONS} is not installed")
    return()
endif()

execute_process(
    COMMAND ${CMAKE_COMMAND} -E env
        QT_QPA_PLATFORM=offscreen
        QT_QUICK_BACKEND=software
        ${APP} --desktop --home --menu-icons ${ICONS} --screenshot-path ${WITH_ICONS}
    RESULT_VARIABLE icons_status
    OUTPUT_QUIET
    ERROR_VARIABLE icons_error)

if(NOT icons_status EQUAL 0)
    message(FATAL_ERROR "the runtime exited with ${icons_status}")
endif()

# An icon the artwork actually ships must load. A file it does not ship is
# reported by the runtime the same way the Python renderer resolves it (a
# `<kind>.png` fallback for an unmapped kind), so that is a gap in the artwork
# rather than a defect in the path -- the pixel check below decides the rest.
file(GLOB installed_icons "${ICONS}/*.png")
foreach(icon ${installed_icons})
    get_filename_component(icon_name "${icon}" NAME)
    string(FIND "${icons_error}" "/${icon_name}" icon_error_position)
    if(NOT icon_error_position EQUAL -1)
        message(FATAL_ERROR "the installed icon ${icon_name} did not load:\n${icons_error}")
    endif()
endforeach()

execute_process(
    COMMAND ${CMAKE_COMMAND} -E env
        QT_QPA_PLATFORM=offscreen
        QT_QUICK_BACKEND=software
        ${APP} --desktop --home --screenshot-path ${WITHOUT_ICONS}
    RESULT_VARIABLE plain_status
    OUTPUT_QUIET
    ERROR_QUIET)

if(NOT plain_status EQUAL 0)
    message(FATAL_ERROR "the runtime exited with ${plain_status}")
endif()

execute_process(
    COMMAND ${PYTHON} ${VERIFY} ${WITH_ICONS} ${WITHOUT_ICONS} ${ICONS}
    RESULT_VARIABLE verify_status
    OUTPUT_VARIABLE verify_output
    ERROR_VARIABLE verify_error)

message(STATUS "${verify_output}${verify_error}")

if(NOT verify_status EQUAL 0)
    message(FATAL_ERROR "the Home rail did not draw its installed artwork")
endif()

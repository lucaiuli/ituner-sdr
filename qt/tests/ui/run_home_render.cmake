# Render the Home screen offscreen and check that the rail, its tiles and its
# instruments actually painted.
#
# Run through `ctest`. The pixel check needs the application runtime (Pillow), so
# it reports a skip rather than a failure when the virtual environment is absent.

if(NOT EXISTS "${PYTHON}")
    message(STATUS "skipping the Home render check: ${PYTHON} is not present")
    return()
endif()

execute_process(
    COMMAND ${CMAKE_COMMAND} -E env
        QT_QPA_PLATFORM=offscreen
        QT_QUICK_BACKEND=software
        ${APP} --desktop --home --screenshot-path ${OUTPUT}
    RESULT_VARIABLE render_status
    OUTPUT_QUIET
    ERROR_QUIET)

if(NOT render_status EQUAL 0)
    message(FATAL_ERROR "the runtime exited with ${render_status}")
endif()

if(NOT EXISTS "${OUTPUT}")
    message(FATAL_ERROR "the runtime wrote no screenshot at ${OUTPUT}")
endif()

execute_process(
    COMMAND ${PYTHON} ${VERIFY} ${OUTPUT}
    RESULT_VARIABLE verify_status
    OUTPUT_VARIABLE verify_output
    ERROR_VARIABLE verify_error)

message(STATUS "${verify_output}${verify_error}")

if(NOT verify_status EQUAL 0)
    message(FATAL_ERROR "the Home screen did not render as the geometry describes")
endif()

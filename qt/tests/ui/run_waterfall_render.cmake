# Render a captured waterfall row set offscreen and compare it with Python.
#
# Run through `ctest`. The check needs the application runtime (Pillow plus the
# renderer module) for the Python side, so it reports a skip rather than a
# failure when the virtual environment is absent -- a clear limitation is better
# than a test that fails for the wrong reason.

if(NOT EXISTS "${PYTHON}")
    message(STATUS "skipping the waterfall render check: ${PYTHON} is not present")
    return()
endif()

execute_process(
    COMMAND ${CMAKE_COMMAND} -E env
        QT_QPA_PLATFORM=offscreen
        QT_QUICK_BACKEND=software
        ${APP} --desktop
               --waterfall-frame ${GOLDEN}
               --waterfall-stream ${STREAM}
               --screenshot-path ${OUTPUT}
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
    COMMAND ${PYTHON} ${VERIFY} ${OUTPUT} ${GOLDEN} ${STREAM}
    RESULT_VARIABLE verify_status
    OUTPUT_VARIABLE verify_output
    ERROR_VARIABLE verify_error)

message(STATUS "${verify_output}${verify_error}")

if(NOT verify_status EQUAL 0)
    message(FATAL_ERROR "the waterfall render does not match the Python reference")
endif()

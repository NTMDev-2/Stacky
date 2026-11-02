// Game of Life
// NTMDev, Nov 1, 2026

define mat [
    [0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0],
    [0, 0, 0, 1, 0, 0],
    [0, 0, 0, 1, 0, 0],
    [0, 0, 0, 1, 0, 0],
    [0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0],
]

function countNeighbors(x, y)
    define total 0

    // Check the row above
    pushlocal y
    calllocalnative dec
    define row poplocal

    if row >= 0
        pushlocal mat
        pushlocal row
        calllocalnative getelement

        pushlocal x
        calllocalnative dec
        define i poplocal
        callnative dump
        loop
            pushlocal [x, 2]
            applylocal add
            if i >= poplocal
                exit 2
            end

            if i >= 0
                calllocalnative dup
                
                pushlocal i
                calllocalnative getelement

                if poplocal == 1
                    pushlocal total
                    calllocalnative inc
                    poplocal total
                end
            end

            pushlocal i
            calllocalnative inc
            poplocal i
        end

        poplocal
    end

    // Check the row below
    pushlocal y
    calllocalnative inc
    set row poplocal

    if row < 7
        pushlocal mat
        pushlocal row
        calllocalnative getelement

        pushlocal x
        calllocalnative dec
        set i poplocal

        loop
            pushlocal [x, 2]
            applylocal add
            if i >= poplocal
                exit 2
            end

            if i >= 0
                calllocalnative dup
                pushlocal i
                calllocalnative getelement

                if poplocal == 1
                    pushlocal total
                    calllocalnative inc
                    poplocal total
                end
            end

            pushlocal i
            calllocalnative inc
            poplocal i
        end

        poplocal
    end

    // Check the cell to the right
    pushlocal [mat, y]
    applylocal getelement
    pushlocal [x, 1]
    applylocal add
    calllocalnative getelement

    if poplocal == 1
        pushlocal total
        calllocalnative inc
        poplocal total
    end

    // Check the cell to the left
    pushlocal [mat, y]
    applylocal getelement
    pushlocal [x, 1]
    applylocal sub
    calllocalnative getelement

    if poplocal == 1
        pushlocal total
        calllocalnative inc
        poplocal total
    end

    return total
end

function staysAlive(x, y)
    call countNeighbors x y
    define neighbors poplocal
    if (neighbors == 2) || (neighbors == 3)
        return true
    end
    return false
end

call staysAlive 3 3 

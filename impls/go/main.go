package main

// Command line:
//
//	<binary> serve --port <PORT> [--sign-key <KEY>] [--replay-window <SECONDS>]
//	               [--core-constraint <TEXT>]...
//	<binary> rank --pool <fixture.json> --mode history|claim
//	              [--own-results <results.json>] [--pool-name <NAME>]
//
// The CLI is a subcommand dispatch so that `rank` (part 2 of this
// implementation) does not change the `serve` flags.

import (
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"strconv"
)

// usage is printed for `-h` and for a bad invocation.
const usage = `usage:
  a2a-go serve --port <PORT> [--sign-key <KEY>] [--replay-window <SECONDS>] [--core-constraint <TEXT>]...
  a2a-go rank --pool <fixture.json> --mode history|claim [--own-results <results.json>] [--pool-name <NAME>]

serve binds 127.0.0.1:<PORT> and answers POST /a2a/message with an
Absorption Result (docs/TRANSPORT.md). --core-constraint is repeatable.

rank applies the selection rules of docs/PREREG_SELECTION.md §7 to a frozen
packet pool and prints {"mode": ..., "ranking": [<packet id>, ...]} (best
first) on stdout. Ties are broken by packet id ascending.
`

// serveOptions is the parsed `serve` configuration.
type serveOptions struct {
	Port            int
	SignKey         string
	ReplayWindow    *float64
	CoreConstraints []string
}

// stringList collects a repeatable flag value.
type stringList []string

func (s *stringList) String() string { return fmt.Sprint([]string(*s)) }

func (s *stringList) Set(v string) error {
	*s = append(*s, v)
	return nil
}

// run is the CLI entry point; it returns the process exit code.
func run(args []string, stdout, stderr io.Writer) int {
	if len(args) == 0 {
		fmt.Fprint(stderr, usage)
		return 2
	}
	switch args[0] {
	case "serve":
		opts, err := parseServeArgs(args[1:])
		if err != nil {
			if errors.Is(err, flag.ErrHelp) {
				fmt.Fprint(stdout, usage)
				return 0
			}
			fmt.Fprintf(stderr, "a2a-go serve: %v\n", err)
			return 2
		}
		return runServe(opts, stderr)
	case "rank":
		return runRank(args[1:], stdout, stderr)
	case "-h", "--help", "help":
		fmt.Fprint(stdout, usage)
		return 0
	default:
		fmt.Fprintf(stderr, "a2a-go: unknown subcommand %q\n%s", args[0], usage)
		return 2
	}
}

// parseServeArgs parses the `serve` flags.
func parseServeArgs(args []string) (*serveOptions, error) {
	fs := flag.NewFlagSet("serve", flag.ContinueOnError)
	fs.SetOutput(io.Discard)
	port := fs.Int("port", 0, "TCP port to bind on 127.0.0.1")
	signKey := fs.String("sign-key", "", "HMAC-SHA256 key; enables signature verification")
	replayWindow := fs.String("replay-window", "", "replay window in seconds; enables replay checks")
	var cores stringList
	fs.Var(&cores, "core-constraint", "core constraint this receiver will not negotiate (repeatable)")
	if err := fs.Parse(args); err != nil {
		return nil, err
	}
	if fs.NArg() > 0 {
		return nil, fmt.Errorf("unexpected argument %q", fs.Arg(0))
	}
	if *port <= 0 || *port > 65535 {
		return nil, fmt.Errorf("--port must be between 1 and 65535")
	}
	opts := &serveOptions{Port: *port, SignKey: *signKey, CoreConstraints: []string(cores)}
	if *replayWindow != "" {
		window, err := strconv.ParseFloat(*replayWindow, 64)
		if err != nil || window < 0 {
			return nil, fmt.Errorf("--replay-window must be a non-negative number of seconds")
		}
		opts.ReplayWindow = &window
	}
	return opts, nil
}

// runServe starts the receiver and blocks.
func runServe(opts *serveOptions, stderr io.Writer) int {
	server := NewServer(opts.SignKey, opts.ReplayWindow, opts.CoreConstraints)
	fmt.Fprintf(stderr, "a2a-go serve: listening on 127.0.0.1:%d (sign=%t replay=%t core_constraints=%d)\n",
		opts.Port, opts.SignKey != "", opts.ReplayWindow != nil, len(opts.CoreConstraints))
	if err := server.ListenAndServe(opts.Port); err != nil {
		fmt.Fprintf(stderr, "a2a-go serve: %v\n", err)
		return 1
	}
	return 0
}

func main() {
	os.Exit(run(os.Args[1:], os.Stdout, os.Stderr))
}

package main

import "fmt"

type Server struct{ port int }

func (s *Server) Start() {
	fmt.Println(s.port)
}

func main() {
	Start()
}

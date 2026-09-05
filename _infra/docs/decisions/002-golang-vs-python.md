# Matik - Golang vs Python

Date: 2025-07-28

Status: `Accepted`

Collaborators: @julie-trias

## Context
Matik is a software platform made up of 6 services. For context, refer to the [https://lucid.app/lucidchart/6c6d807b-ca61-4be7-b571-897e35bd9712/edit?viewport_loc=-459%2C-30%2C4353%2C3420%2CyJO_wFNQ_qPY&invitationId=inv_16bf17b0-a22f-4cdf-b908-9e90f06fc47c](Matik C4 document) to understand the microservices that make up the Matik Platform.

### Option I - Python
#### Pros
- Extensive Libraries and Frameworks
    - Python boasts a massive and mature ecosystem with a rich collection of libraries and frameworks for nearly every use case, including web development (Django, Flask), data science (NumPy, pandas), machine learning (TensorFlow, PyTorch), and more, says Mobilunity. - This simplifies development, reduces time-to-market, and allows developers to leverage pre-built functionalities
- Ease of Use and Readability
    - Python's syntax emphasizes readability and simplicity, making it highly accessible for beginners and promoting rapid development, notes Python in Plain English. Its reliance on indentation rather than braces for code blocks contributes to its clean and intuitive feel
- Rapid Prototyping
    - Python's dynamic typing and interpreted nature allow for quick experimentation and iterative development, making it an ideal choice for turning ideas into working prototypes swiftly.
- Versatility
    - Python's adaptability across various domains, including web development, data science, machine learning, AI, automation, and scripting, makes it a highly sought-after skill and suitable for a wide range of projects.
- Large and Active Community
    - Python benefits from a vast and active developer community, offering extensive support, resources, and documentation, says TheOneTechnologies. This makes finding solutions to problems and learning the language easier, particularly for new developers

#### Cons
- Performance
    - Python is generally slower than Go due to its interpreted nature and the Global Interpreter Lock (GIL) in its primary implementation (CPython), which restricts true parallelism in multi-threaded programs. This can be a significant drawback for performance-critical applications and CPU-bound tasks
- Concurrency Limitations
    - While Python offers concurrency through libraries like asyncio and multiprocessing, it doesn't have the same built-in, lightweight concurrency model as Go's goroutines, which can be less efficient, especially for CPU-bound tasks
- Memory Efficiency
    - Python's dynamic typing and garbage collection can sometimes be less memory-efficient than Go, which can be a concern for large-scale applications or those with memory constraints
- Dependency Management
    - While Python has powerful package managers, managing dependencies in complex projects can become cumbersome, notes Reddit.
Potential for Runtime Errors: Python's dynamic typing offers flexibility but can also lead to runtime errors that may be missed during development, requiring more thorough testing and debugging compared to statically-typed languages like Go

### Option II - Golang
#### Pros
- Performance
    - Compiled Language: Go compiles directly to machine code, eliminating the need for an interpreter (like Python and Ruby) or a Java Virtual Machine (JVM). This results in faster execution speeds and lower latency compared to interpreted or JVM-based languages
    - Static Typing: Go is statically typed, meaning variable types are checked at compile time. This helps catch errors earlier in the development cycle, potentially leading to more robust and performant code. In contrast, Python and Ruby are dynamically typed, which can offer flexibility but may introduce runtime errors related to type mismatches
- Concurrency
    - Goroutines and Channels: Go's built-in concurrency model, using goroutines and channels, is a major strength. Goroutines are lightweight threads managed by the Go runtime, making it efficient to handle numerous concurrent tasks. Channels provide a safe and structured way for goroutines to communicate, reducing the risk of race conditions
    - Efficient Parallel Processing: Go's runtime manages goroutines, minimizing the overhead of context switching and making parallel processing efficient, particularly for applications with high workloads. Python's Global Interpreter Lock (GIL) can limit true parallel execution in multi-threaded applications, and Java's thread-based concurrency can be more resource-intensive compared to Go's goroutines
- Simplicity and Readability
    - Minimalist Syntax: Go's simple and concise syntax emphasizes clarity and maintainability, making it easier to learn and read. This contrasts with Java's more verbose syntax, which can lead to a steeper learning curve
    - Faster Development: Go's rapid compilation times facilitate quicker iteration and deployment, which can be a significant advantage in fast-paced development environments. The simple syntax also contributes to faster development cycles
- Resource Utilization and Memory Management
    - Memory Efficiency: Go applications generally consume less memory compared to Java, partly due to Go's design principles that favor minimalistic constructs and efficient data handling. Go's garbage collector is optimized for concurrent workloads and helps prevent memory leaks
    - Efficient for Microservices: Go's performance, low memory overhead, and concurrency support make it a popular choice for building microservices and high-performance backend services
- Modern Development and Deployment
    - Cloud-Native and Distributed Systems: Go was designed with features suitable for building large-scale, scalable systems, such as lightweight goroutines and efficient garbage collection, according to The Go Programming Language. Go's static compilation allows for the creation of single binary files without external dependencies, simplifying deployment and distribution. This ease of deployment can be beneficial in containerized and cloud-native environments
    - Strong Tooling: Go's tooling includes a compiler, formatter, and package manager, emphasizing standardization and simplicity. Go also has built-in testing, benchmarking, and profiling frameworks
- Speed of Execution
    - Since I have been coding mostly in Golang in the most recent 4 years, I can write the code fast and mentor the team at the same time

### Cons
- Smaller Ecosystem/Library Support
    - Compared to Python, Go's library and framework ecosystem is less mature and extensive, particularly in specialized domains like data science and machine learning. Finding a specific library or tool might be more challenging or require building custom solutions.
- Steeper Learning Curve (initially)
    - While Go aims for simplicity, its syntax, particularly its explicit error handling and use of pointers, can be slightly more verbose and challenging for beginners compared to Python's more intuitive and concise syntax
- Less Versatility for General-Purpose Tasks
    - While Go is a general-purpose language, its strengths lie primarily in systems programming, web services, and backend development. Python's flexibility makes it more widely adopted for diverse tasks like scripting, automation, and data analysis

## Decision
Matik's backend will be primarily written in Golang. While Python excels in rapid prototyping and data science, Go stands out when performance, concurrency, and efficient resource utilization are critical, especially for backend services, microservices, and cloud-native applications, making it better suited for Matik.

Not to mention that our team is excited to learn and code in Go. Upskill!

## Considerations
Choosing the right programming language for your application is crucial and depends heavily on your project's specific requirements. While Java, Python, and Ruby each have their strengths, Go (Golang) offers distinct advantages for Matik in certain scenarios, especially when performance, concurrency, and efficient resource utilization are paramount.

<sup>*</sup>*Important: Although, Golang will be used for Matik's backend services, Python will be used for its machine learning algorithm/service*

## Consequences
The team will upskill and not only by learning Go but by learning how to develop an enterprise-ready product.
